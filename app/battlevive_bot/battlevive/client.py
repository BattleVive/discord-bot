from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from collections.abc import Sequence
from pathlib import Path
from typing import Callable
from typing import Protocol
from typing import TypeVar

import aiohttp

from .supabase import SupabaseTransport
from .tokens import TokenPair
from .tokens import TokenStore
from .tokens import TokenStoreProtocol
from ..logs import logger
from ..models import Lobby
from ..models import LobbyCaptain
from ..models import LobbyDraftAction
from ..models import MatchResultConfirmation
from ..models import SeasonRating
from ..models import User
from ..models import UserTrophy
from ..models import parse_lobbies
from ..models import parse_lobby_captains
from ..models import parse_lobby_draft_actions
from ..models import parse_lobby_roster_members
from ..models import parse_match_result_confirmations
from ..models import parse_season_ratings
from ..models import parse_user_trophies
from ..models import parse_users


ParsedT = TypeVar("ParsedT")
Parser = Callable[[object], list[ParsedT]]


class ActiveLobbyAPI(Protocol):
    async def get_lobby_draft_actions(
        self,
        lobby_id: int,
    ) -> list[LobbyDraftAction]: ...

    async def get_lobby_captains(self, lobby_id: int) -> list[LobbyCaptain]: ...

    async def get_match_result_confirmations(
        self,
        lobby_id: int,
    ) -> list[MatchResultConfirmation]: ...


class BattleviveClient:
    def __init__(
        self,
        *,
        bootstrap_access_token: str | None,
        bootstrap_refresh_token: str | None,
        token_path: Path | str,
        supabase_url: str,
        supabase_api_key: str,
        transport: SupabaseTransport | None = None,
        token_store: TokenStoreProtocol | None = None,
    ) -> None:
        self._token_store = token_store or TokenStore(token_path)
        persisted_tokens = self._token_store.load()
        self._bootstrap_tokens = TokenPair.from_values(
            bootstrap_access_token,
            bootstrap_refresh_token,
        )
        self._tokens = persisted_tokens or self._bootstrap_tokens
        if self._tokens is None:
            raise ValueError("A complete Battlevive token pair is required")
        self._refresh_fallback_available = (
            persisted_tokens is not None and self._bootstrap_tokens is not None
        )

        path = getattr(self._token_store, "path", None)
        try:
            token_path_exists = path is not None and path.lstat() is not None
        except OSError:
            token_path_exists = False
        if persisted_tokens is None and token_path_exists:
            logger.warning(
                "Ignoring invalid Battlevive token state at %s",
                path,
            )

        self._transport = transport or SupabaseTransport(
            supabase_url,
            supabase_api_key,
        )
        self._refresh_lock = asyncio.Lock()
        self._persistence_degraded = False

    @property
    def persistence_degraded(self) -> bool:
        return self._persistence_degraded

    async def get_users(self) -> list[User]:
        return await self._get_and_parse("users", parse_users)

    async def get_lobbies(self) -> list[Lobby]:
        lobbies, roster_members = await asyncio.gather(
            self._get_and_parse("matches", parse_lobbies),
            self._get_and_parse(
                "match_slots",
                parse_lobby_roster_members,
                params=(
                    ("select", "match_id,user_id,slot"),
                    ("order", "joined_at.asc,id.asc"),
                ),
            ),
        )
        by_lobby = {lobby.id: lobby for lobby in lobbies}
        for lobby in lobbies:
            lobby.team_one_roster = []
            lobby.team_two_roster = []
        for member in roster_members:
            lobby = by_lobby.get(member.lobby_id)
            if lobby is not None:
                getattr(lobby, f"{member.slot}_roster").append(member.user_id)
        return lobbies

    async def get_season_ratings(self) -> list[SeasonRating]:
        return await self._get_and_parse(
            "season_ratings",
            parse_season_ratings,
        )

    async def get_user_trophies(self) -> list[UserTrophy]:
        return await self._get_and_parse(
            "user_trophies",
            parse_user_trophies,
        )

    async def get_lobby_draft_actions(
        self,
        lobby_id: int,
    ) -> list[LobbyDraftAction]:
        return await self._get_and_parse(
            "match_draft_actions",
            parse_lobby_draft_actions,
            params=(
                (
                    "select",
                    "id,match_id,step,team_slot,action,champion,created_at",
                ),
                ("match_id", f"eq.{lobby_id}"),
                ("order", "step.asc"),
            ),
        )

    async def get_lobby_captains(self, lobby_id: int) -> list[LobbyCaptain]:
        return await self._get_and_parse(
            "match_slots",
            parse_lobby_captains,
            params=(
                ("select", "user_id,slot"),
                ("match_id", f"eq.{lobby_id}"),
                ("is_captain", "eq.true"),
            ),
        )

    async def get_match_result_confirmations(
        self,
        lobby_id: int,
    ) -> list[MatchResultConfirmation]:
        return await self._get_and_parse(
            "match_result_confirmations",
            parse_match_result_confirmations,
            params=(
                (
                    "select",
                    "id,match_id,user_id,selected_winner,created_at,captain_slot",
                ),
                ("match_id", f"eq.{lobby_id}"),
                ("order", "created_at.asc"),
            ),
        )

    async def refresh_credentials(self) -> None:
        async with self._refresh_lock:
            await self._refresh_credentials()

    async def close(self) -> None:
        await self._transport.close()

    async def _get_and_parse(
        self,
        endpoint: str,
        parser: Parser[ParsedT],
        *,
        params: Mapping[str, str] | Sequence[tuple[str, str]] | None = None,
    ) -> list[ParsedT]:
        start = time.perf_counter()
        failed_access_token = self._tokens.access_token

        try:
            raw = await self._transport_get(
                endpoint,
                failed_access_token,
                params,
            )
        except aiohttp.ClientResponseError as error:
            if error.status != 401:
                raise
            await self._refresh_after_unauthorized(failed_access_token)
            raw = await self._transport_get(
                endpoint,
                self._tokens.access_token,
                params,
            )

        try:
            result = parser(raw)
        except (KeyError, TypeError, ValueError) as error:
            logger.error("Failed to parse %s response: %s", endpoint, error)
            raise

        elapsed = time.perf_counter() - start
        logger.info("Fetched %d %s in %.2fs", len(result), endpoint, elapsed)
        return result

    async def _transport_get(
        self,
        endpoint: str,
        access_token: str,
        params: Mapping[str, str] | Sequence[tuple[str, str]] | None,
    ) -> object:
        if params is None:
            return await self._transport.get(endpoint, access_token)
        return await self._transport.get(
            endpoint,
            access_token,
            params=params,
        )

    async def _refresh_after_unauthorized(
        self,
        failed_access_token: str,
    ) -> None:
        async with self._refresh_lock:
            if self._tokens.access_token != failed_access_token:
                return
            await self._refresh_credentials()

    async def _refresh_credentials(self) -> None:
        try:
            refreshed_tokens = await self._transport.refresh(
                self._tokens.refresh_token
            )
        except aiohttp.ClientResponseError as error:
            if (
                error.status != 400
                or not self._refresh_fallback_available
                or self._bootstrap_tokens is None
                or (
                    self._bootstrap_tokens.refresh_token
                    == self._tokens.refresh_token
                )
            ):
                raise
            self._refresh_fallback_available = False
            logger.warning(
                "Persisted Battlevive refresh token was rejected; "
                "retrying bootstrap credentials."
            )
            refreshed_tokens = await self._transport.refresh(
                self._bootstrap_tokens.refresh_token
            )
        else:
            self._refresh_fallback_available = False
        self._tokens = refreshed_tokens

        try:
            self._token_store.save(refreshed_tokens)
        except OSError:
            self._persistence_degraded = True
            logger.critical(
                "Failed to persist refreshed Battlevive credentials; health is degraded."
            )
        else:
            self._persistence_degraded = False
            logger.info("Refreshed and persisted Battlevive credentials")

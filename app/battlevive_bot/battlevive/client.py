from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Callable
from typing import TypeVar

import aiohttp

from .supabase import SupabaseTransport
from .tokens import TokenPair
from .tokens import TokenStore
from ..logs import logger
from ..models import Lobby
from ..models import SeasonRating
from ..models import User
from ..models import UserTrophy
from ..models import parse_lobbies
from ..models import parse_season_ratings
from ..models import parse_user_trophies
from ..models import parse_users


ParsedT = TypeVar("ParsedT")
Parser = Callable[[object], list[ParsedT]]


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
    ) -> None:
        self._token_store = TokenStore(token_path)
        persisted_tokens = self._token_store.load()
        bootstrap_tokens = TokenPair.from_values(
            bootstrap_access_token,
            bootstrap_refresh_token,
        )
        self._tokens = persisted_tokens or bootstrap_tokens
        if self._tokens is None:
            raise ValueError("A complete Battlevive token pair is required")

        if persisted_tokens is None and self._token_store.path.exists():
            logger.warning(
                "Ignoring invalid Battlevive token state at %s",
                self._token_store.path,
            )

        self._transport = transport or SupabaseTransport(
            supabase_url,
            supabase_api_key,
        )
        self._refresh_lock = asyncio.Lock()

    async def get_users(self) -> list[User]:
        return await self._get_and_parse("users", parse_users)

    async def get_lobbies(self) -> list[Lobby]:
        return await self._get_and_parse("lobbies", parse_lobbies)

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

    async def refresh_credentials(self) -> None:
        async with self._refresh_lock:
            await self._refresh_credentials()

    async def close(self) -> None:
        await self._transport.close()

    async def _get_and_parse(
        self,
        endpoint: str,
        parser: Parser[ParsedT],
    ) -> list[ParsedT]:
        start = time.perf_counter()
        failed_access_token = self._tokens.access_token

        try:
            raw = await self._transport.get(endpoint, failed_access_token)
        except aiohttp.ClientResponseError as error:
            if error.status != 401:
                raise
            await self._refresh_after_unauthorized(failed_access_token)
            raw = await self._transport.get(
                endpoint,
                self._tokens.access_token,
            )

        try:
            result = parser(raw)
        except (KeyError, TypeError, ValueError) as error:
            logger.error("Failed to parse %s response: %s", endpoint, error)
            raise

        elapsed = time.perf_counter() - start
        logger.info("Fetched %d %s in %.2fs", len(result), endpoint, elapsed)
        return result

    async def _refresh_after_unauthorized(
        self,
        failed_access_token: str,
    ) -> None:
        async with self._refresh_lock:
            if self._tokens.access_token != failed_access_token:
                return
            await self._refresh_credentials()

    async def _refresh_credentials(self) -> None:
        refreshed_tokens = await self._transport.refresh(
            self._tokens.refresh_token
        )
        self._tokens = refreshed_tokens

        try:
            self._token_store.save(refreshed_tokens)
        except OSError:
            logger.exception(
                "Failed to persist refreshed Battlevive credentials to %s",
                self._token_store.path,
            )
        else:
            logger.info("Refreshed and persisted Battlevive credentials")

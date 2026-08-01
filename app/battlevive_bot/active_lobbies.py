from __future__ import annotations

import asyncio
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import time
from typing import Any
from typing import Protocol
from urllib.parse import quote
from urllib.parse import urlsplit

import asyncpg
import discord

from .logs import logger


ACTIVE_LOBBY_NOTIFY_CHANNEL = "active_lobby_changed"
ACTIVE_LOBBY_POLL_SECONDS = 5.0
ACTIVE_LOBBY_IDLE_SECONDS = 30.0
ACTIVE_LOBBY_MAX_BACKOFF_SECONDS = 60.0
ACTIVE_LOBBY_MAP_MANIFEST = Path("maps/manifest.json")
MAX_FIELD_VALUE = 1024
MAX_FIELD_NAME = 256
MAX_EMBED_TITLE = 256


class ActiveLobbyAPI(Protocol):
    async def get_lobby_draft_actions(self, lobby_id: int) -> list[Any]: ...

    async def get_lobby_captains(self, lobby_id: int) -> list[Any]: ...

    async def get_match_result_confirmations(self, lobby_id: int) -> list[Any]: ...


class ActiveLobbyDatabase(Protocol):
    async def get_active_lobby_candidates(self) -> list[dict[str, Any]]: ...

    async def update_lobby_captains(
        self,
        lobby_id: int,
        captains: Sequence[Any],
    ) -> None: ...

    async def finalize_lobby_draft(
        self,
        lobby_id: int,
        actions: Sequence[Any],
        finalized_at: datetime | None = None,
    ) -> None: ...

    async def get_configured_active_lobbies(self) -> list[dict[str, Any]]: ...

    async def set_active_lobby_channel(
        self,
        guild_id: int,
        channel_id: int | None,
        updated_by: int,
    ) -> None: ...

    async def set_active_lobby_role(
        self,
        guild_id: int,
        role_id: int | None,
        updated_by: int,
    ) -> None: ...

    async def reset_active_lobby_config(
        self,
        guild_id: int,
        updated_by: int,
    ) -> None: ...

    async def get_active_lobby_post_states(
        self,
        guild_id: int,
    ) -> list[dict[str, Any]]: ...

    async def ensure_active_lobby_post_states(
        self,
        guild_id: int,
        lobby_ids: Sequence[int],
        *,
        notification_handled: bool,
    ) -> None: ...

    async def record_active_lobby_post(
        self,
        guild_id: int,
        lobby_id: int,
        channel_id: int,
        message_id: int,
        fingerprint: str,
        *,
        notification_handled: bool,
    ) -> None: ...

    async def clear_active_lobby_post_message(
        self,
        guild_id: int,
        lobby_id: int,
        channel_id: int,
        message_id: int,
    ) -> bool: ...

    async def get_active_lobby_empty_post(
        self,
        guild_id: int,
    ) -> dict[str, Any] | None: ...

    async def record_active_lobby_empty_post(
        self,
        guild_id: int,
        channel_id: int,
        message_id: int,
        fingerprint: str,
    ) -> None: ...

    async def clear_active_lobby_empty_post(
        self,
        guild_id: int,
        channel_id: int,
        message_id: int,
    ) -> bool: ...


@dataclass(slots=True)
class LobbyPollState:
    actions: list[Any] = field(default_factory=list)
    captains: dict[str, Any] = field(default_factory=dict)
    confirmations: list[Any] = field(default_factory=list)
    draft_finalized: bool = False
    failures: int = 0
    retry_at: float = 0.0


@dataclass(frozen=True, slots=True)
class RenderedLobby:
    embed: discord.Embed
    fingerprint: str
    map_path: Path | None = None
    attachment_name: str | None = None

    def file(self) -> discord.File | None:
        if self.map_path is None or self.attachment_name is None:
            return None
        return discord.File(self.map_path, filename=self.attachment_name)


@dataclass(frozen=True, slots=True)
class ResolvedMap:
    name: str
    path: Path | None


def _get(value: object, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _normalized_name(value: object) -> str:
    return "-".join(re.findall(r"[a-z0-9]+", str(value).casefold()))


def _normalized_slot(value: object) -> str:
    normalized = _normalized_name(value).replace("-", "_")
    aliases = {
        "one": "team_one",
        "1": "team_one",
        "team1": "team_one",
        "team_one": "team_one",
        "two": "team_two",
        "2": "team_two",
        "team2": "team_two",
        "team_two": "team_two",
    }
    return aliases.get(normalized, normalized)


def _truncate(value: object, limit: int) -> str:
    text = str(value)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def validate_battlevive_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "BATTLEVIVE_URL must be an HTTPS URL without credentials, query, or fragment."
        )
    return value.strip().rstrip("/")


def build_match_url(lobby: Mapping[str, Any], base_url: str) -> str | None:
    year = lobby.get("url_year")
    series = lobby.get("url_series")
    game = lobby.get("game_number")
    if year is None or not series or game is None:
        return None
    return (
        f"{base_url}/matchmaking/{quote(str(year), safe='')}/"
        f"{quote(str(series), safe='')}/GAME-{quote(str(game), safe='')}"
    )


class MapResolver:
    """Resolve upstream map aliases through the data-owned manifest."""

    def __init__(self, assets_root: Path, manifest: Path | None = None) -> None:
        self.assets_root = Path(assets_root)
        self.manifest_path = (
            Path(manifest)
            if manifest is not None
            else self.assets_root / ACTIVE_LOBBY_MAP_MANIFEST
        )
        self._loaded = False
        self._aliases: dict[str, tuple[str, Path | None, Path | None]] = {}

    def resolve(self, selected_map: str | None) -> ResolvedMap | None:
        if not selected_map:
            return None
        self._load()
        normalized = _normalized_name(selected_map)
        variant = None
        for suffix in ("-day", "-night"):
            if normalized.endswith(suffix):
                normalized = normalized[: -len(suffix)]
                variant = suffix[1:]
                break
        entry = self._aliases.get(normalized)
        if entry is None:
            return ResolvedMap(str(selected_map), None)
        name, day_path, night_path = entry
        path = night_path if variant == "night" else day_path
        return ResolvedMap(name, path if path is not None and path.is_file() else None)

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            logger.warning(
                "Active-lobby map manifest is unavailable; map thumbnails disabled."
            )
            return

        entries = payload.get("maps", []) if isinstance(payload, Mapping) else []
        for item in entries:
            if not isinstance(item, Mapping) or not item.get("name"):
                continue
            name = str(item["name"])
            day_path = self._asset_path(item.get("day"))
            night_path = self._asset_path(item.get("night"))
            aliases = [name, *(item.get("aliases") or [])]
            for alias in aliases:
                key = _normalized_name(alias)
                if key:
                    self._aliases[key] = (name, day_path, night_path)

    def _asset_path(self, value: object) -> Path | None:
        if not value:
            return None
        path = Path(str(value))
        if path.is_absolute():
            return path
        manifest_relative = self.manifest_path.parent / path
        if manifest_relative.is_file():
            return manifest_relative
        return self.assets_root / path


def application_emoji_lookup(emojis: Sequence[object]) -> dict[str, str]:
    result: dict[str, str] = {}
    for emoji in emojis:
        name = getattr(emoji, "name", None)
        key = _normalized_name(name) if name else ""
        if key:
            result[key] = str(emoji)
    return result


def _champion_line(champion: str, emoji_lookup: Mapping[str, str]) -> str:
    emoji = emoji_lookup.get(_normalized_name(champion))
    return f"{emoji} {champion}" if emoji else champion


def _users(candidate: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rows = candidate.get("users_by_id") or {}
    return {str(key): value for key, value in rows.items()}


def _roster_lines(
    candidate: Mapping[str, Any],
    slot: str,
    captain_id: str | None,
) -> list[str]:
    users = _users(candidate)
    result: list[str] = []
    for user_id in candidate.get(f"{slot}_roster") or []:
        key = str(user_id)
        user = users.get(key, {})
        discord_id = user.get("discord_id")
        username = user.get("discord_username") or key
        label = f"<@{discord_id}>" if discord_id is not None else str(username)
        if captain_id is not None and key == str(captain_id):
            label = f"👑 {label}"
        result.append(label)
    return result or ["No roster available"]


def _team_draft(
    candidate: Mapping[str, Any],
    state: LobbyPollState,
    slot: str,
) -> tuple[list[str], list[str]]:
    picks: list[str] = []
    bans: list[str] = []
    for action in sorted(state.actions, key=lambda row: int(_get(row, "step", 0))):
        if _normalized_slot(_get(action, "team_slot")) != slot:
            continue
        champion = _get(action, "champion")
        if not champion:
            continue
        action_name = _normalized_name(_get(action, "action"))
        if action_name == "pick":
            picks.append(str(champion))
        elif action_name == "ban":
            bans.append(str(champion))
    if not picks:
        picks = [str(value) for value in candidate.get(f"{slot}_picks") or []]
    if not bans:
        bans = [str(value) for value in candidate.get(f"{slot}_bans") or []]
    return picks, bans


def newest_confirmations(confirmations: Sequence[Any]) -> dict[str, Any]:
    newest: dict[str, Any] = {}
    for confirmation in confirmations:
        slot = _normalized_slot(_get(confirmation, "captain_slot"))
        if slot not in ("team_one", "team_two"):
            continue
        current = newest.get(slot)
        created_at = _get(confirmation, "created_at")
        if current is None or created_at >= _get(current, "created_at"):
            newest[slot] = confirmation
    return newest


def _team_name(candidate: Mapping[str, Any], slot: str) -> str:
    fallback = "Team One" if slot == "team_one" else "Team Two"
    return str(candidate.get(f"{slot}_name") or fallback)


def result_confirmation_text(
    candidate: Mapping[str, Any],
    confirmations: Sequence[Any],
) -> str:
    votes = newest_confirmations(confirmations)
    if not votes:
        return "⏳ Waiting for both captains"

    lines: list[str] = []
    winners: dict[str, str] = {}
    for slot in ("team_one", "team_two"):
        captain_name = _team_name(candidate, slot)
        confirmation = votes.get(slot)
        if confirmation is None:
            lines.append(f"{captain_name} captain: ⏳ Waiting")
            continue
        winner = _normalized_slot(_get(confirmation, "selected_winner"))
        winners[slot] = winner
        lines.append(
            f"{captain_name} captain: ✅ {_team_name(candidate, winner)}"
        )

    if len(winners) == 2 and len(set(winners.values())) > 1:
        return "⚠️ **Disputed**\n" + "\n".join(lines)
    return "\n".join(lines)


def result_is_resolved(candidate: Mapping[str, Any], state: LobbyPollState) -> bool:
    winner_slot = _normalized_slot(candidate.get("winner_slot"))
    if winner_slot in ("team_one", "team_two") and (
        candidate.get("dispute_reason")
        or "disput" in str(candidate.get("status", "")).casefold()
    ):
        return True
    votes = newest_confirmations(state.confirmations)
    if len(votes) != 2:
        return False
    winners = {
        _normalized_slot(_get(confirmation, "selected_winner"))
        for confirmation in votes.values()
    }
    return len(winners) == 1 and next(iter(winners)) in ("team_one", "team_two")


def _phase(candidate: Mapping[str, Any], state: LobbyPollState) -> str:
    if candidate.get("dispute_reason") or "disput" in str(
        candidate.get("status", "")
    ).casefold():
        return "⚠️ Disputed"
    if candidate.get("ended_at") is not None:
        return "Awaiting result confirmation"
    if state.actions or candidate.get("draft_started_at") is not None:
        step = candidate.get("draft_step")
        return f"Draft · Step {step}" if step is not None else "Draft"
    return _truncate(str(candidate.get("status") or "Active").replace("_", " ").title(), 100)


def build_active_lobby_embed(
    candidate: Mapping[str, Any],
    state: LobbyPollState,
    *,
    emoji_lookup: Mapping[str, str],
    map_resolver: MapResolver,
    battlevive_url: str,
) -> RenderedLobby:
    lobby_number = candidate.get("lobby_number", candidate.get("id", "?"))
    game_number = candidate.get("game_number")
    title = (
        f"Game {game_number} · Lobby #{lobby_number}"
        if game_number is not None
        else f"Lobby #{lobby_number}"
    )
    embed = discord.Embed(
        title=_truncate(title, MAX_EMBED_TITLE),
        url=build_match_url(candidate, battlevive_url),
        description=_phase(candidate, state),
        colour=discord.Colour.blurple(),
    )

    for slot in ("team_one", "team_two"):
        captain = state.captains.get(slot)
        captain_id = (
            str(_get(captain, "user_id"))
            if captain is not None
            else (
                str(candidate[f"{slot}_captain_id"])
                if candidate.get(f"{slot}_captain_id") is not None
                else None
            )
        )
        lines = _roster_lines(candidate, slot, captain_id)
        picks, bans = _team_draft(candidate, state, slot)
        if picks:
            lines.extend(
                ["", "**Picks**", *[_champion_line(value, emoji_lookup) for value in picks]]
            )
        if bans:
            lines.extend(
                ["", "**Bans**", *[_champion_line(value, emoji_lookup) for value in bans]]
            )
        embed.add_field(
            name=_truncate(_team_name(candidate, slot), MAX_FIELD_NAME),
            value=_truncate("\n".join(lines), MAX_FIELD_VALUE),
            inline=True,
        )

    selected_map = candidate.get("selected_map")
    resolved_map = map_resolver.resolve(str(selected_map) if selected_map else None)
    if selected_map:
        map_name = resolved_map.name if resolved_map is not None else str(selected_map)
        embed.add_field(name="Selected Map", value=_truncate(map_name, MAX_FIELD_VALUE))

    if candidate.get("ended_at") is not None or state.confirmations:
        result_text = result_confirmation_text(candidate, state.confirmations)
        winner_slot = _normalized_slot(candidate.get("winner_slot"))
        if (
            winner_slot in ("team_one", "team_two")
            and (candidate.get("dispute_reason") or "disput" in str(candidate.get("status", "")).casefold())
        ):
            result_text = f"✅ Moderator resolved: {_team_name(candidate, winner_slot)}"
        embed.add_field(
            name="Result Confirmation",
            value=_truncate(result_text, MAX_FIELD_VALUE),
            inline=False,
        )

    map_path = resolved_map.path if resolved_map is not None else None
    attachment_name = None
    if map_path is not None:
        attachment_name = f"map-{_normalized_name(map_path.stem)}.png"
        embed.set_thumbnail(url=f"attachment://{attachment_name}")

    payload = embed.to_dict()
    fingerprint_data = {
        "embed": payload,
        "map": str(map_path) if map_path is not None else None,
    }
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return RenderedLobby(embed, fingerprint, map_path, attachment_name)


def build_empty_embed() -> tuple[discord.Embed, str]:
    embed = discord.Embed(
        title="Active Lobbies",
        description="There are no active lobbies right now.",
        colour=discord.Colour.dark_grey(),
    )
    fingerprint = hashlib.sha256(
        json.dumps(embed.to_dict(), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return embed, fingerprint


class ActiveLobbyService:
    """Poll active lobbies once globally and reconcile every configured guild."""

    def __init__(
        self,
        bot: Any,
        api: ActiveLobbyAPI,
        database: ActiveLobbyDatabase,
        dsn: str | None,
        battlevive_url: str,
        assets_root: Path,
        *,
        poll_interval: float = ACTIVE_LOBBY_POLL_SECONDS,
        idle_interval: float = ACTIVE_LOBBY_IDLE_SECONDS,
        max_backoff: float = ACTIVE_LOBBY_MAX_BACKOFF_SECONDS,
        clock: Any = time.monotonic,
    ) -> None:
        self.bot = bot
        self.api = api
        self.database = database
        self.dsn = dsn
        self.battlevive_url = validate_battlevive_url(battlevive_url)
        self.map_resolver = MapResolver(assets_root)
        self.poll_interval = poll_interval
        self.idle_interval = idle_interval
        self.max_backoff = max_backoff
        self.clock = clock
        self.polling_active = False
        self._states: dict[int, LobbyPollState] = {}
        self._emoji_lookup: dict[str, str] = {}
        self._emojis_loaded = False
        self._requested = asyncio.Event()
        self._closed = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []
        self._listener_connection: asyncpg.Connection | None = None
        self._reconcile_lock = asyncio.Lock()

    def start(self) -> None:
        if self._tasks:
            return
        self._closed.clear()
        self._tasks = [
            asyncio.create_task(self._worker(), name="active-lobby-worker"),
            asyncio.create_task(self._listen(), name="active-lobby-listener"),
        ]
        self.request_reconciliation()

    async def stop(self) -> None:
        if not self._tasks:
            return
        self._closed.set()
        self._requested.set()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self.polling_active = False
        await self._close_listener()

    def request_reconciliation(self, *_args: object) -> None:
        self._requested.set()

    async def reconcile_once(self) -> None:
        async with self._reconcile_lock:
            configs, candidates = await asyncio.gather(
                self.database.get_configured_active_lobbies(),
                self.database.get_active_lobby_candidates(),
            )
            has_channel = any(
                config.get("active_lobby_channel_id") is not None
                for config in configs
            )
            self.polling_active = bool(has_channel and candidates)
            if self.polling_active:
                await self._refresh_application_emojis()
                await self._poll_candidates(candidates)

            candidate_ids = {int(candidate["id"]) for candidate in candidates}
            self._states = {
                lobby_id: state
                for lobby_id, state in self._states.items()
                if lobby_id in candidate_ids
            }

            for config in configs:
                try:
                    await self._reconcile_guild(config, candidates)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception(
                        "Active-lobby reconciliation failed for guild %s.",
                        config.get("guild_id"),
                    )

    async def _poll_candidates(self, candidates: Sequence[dict[str, Any]]) -> None:
        await asyncio.gather(
            *(self._poll_lobby(candidate) for candidate in candidates)
        )

    async def _poll_lobby(self, candidate: dict[str, Any]) -> None:
        lobby_id = int(candidate["id"])
        state = self._states.setdefault(lobby_id, LobbyPollState())
        state.draft_finalized = bool(
            state.draft_finalized or candidate.get("draft_finalized_at")
        )
        for slot in ("team_one", "team_two"):
            captain_id = candidate.get(f"{slot}_captain_id")
            if captain_id is not None and slot not in state.captains:
                state.captains[slot] = {
                    "slot": slot,
                    "user_id": str(captain_id),
                }

        if self.clock() < state.retry_at:
            return

        failed = False
        ended = candidate.get("ended_at") is not None
        try:
            if not ended or not state.draft_finalized:
                actions = await self.api.get_lobby_draft_actions(lobby_id)
                state.actions = list(actions)
                if ended:
                    await self.database.finalize_lobby_draft(lobby_id, state.actions)
                    state.draft_finalized = True
        except asyncio.CancelledError:
            raise
        except Exception:
            failed = True
            logger.warning(
                "Active-lobby draft refresh failed for lobby %s; retaining cached state.",
                lobby_id,
            )

        if len(state.captains) < 2:
            try:
                captains = await self.api.get_lobby_captains(lobby_id)
                valid = {
                    _normalized_slot(_get(captain, "slot")): captain
                    for captain in captains
                    if _normalized_slot(_get(captain, "slot"))
                    in ("team_one", "team_two")
                }
                if valid:
                    state.captains.update(valid)
                    await self.database.update_lobby_captains(
                        lobby_id,
                        list(valid.values()),
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                failed = True
                logger.warning(
                    "Active-lobby captain refresh failed for lobby %s; retaining cached state.",
                    lobby_id,
                )

        if ended:
            try:
                confirmations = await self.api.get_match_result_confirmations(lobby_id)
                state.confirmations = list(confirmations)
            except asyncio.CancelledError:
                raise
            except Exception:
                failed = True
                logger.warning(
                    "Active-lobby confirmation refresh failed for lobby %s; retaining cached state.",
                    lobby_id,
                )

        if failed:
            state.failures += 1
            delay = min(self.poll_interval * (2 ** (state.failures - 1)), self.max_backoff)
            state.retry_at = self.clock() + delay
        else:
            state.failures = 0
            state.retry_at = 0.0

    async def _reconcile_guild(
        self,
        config: dict[str, Any],
        candidates: Sequence[dict[str, Any]],
    ) -> None:
        guild_id = int(config["guild_id"])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise RuntimeError("configured guild is not available to the bot")

        stored = await self.database.get_active_lobby_post_states(guild_id)
        empty = await self.database.get_active_lobby_empty_post(guild_id)
        channel_id = config.get("active_lobby_channel_id")
        if channel_id is None:
            await self._remove_all_posts(guild, stored, empty)
            return

        channel = guild.get_channel(int(channel_id))
        if channel is None:
            raise RuntimeError("configured active-lobby channel is not available")
        if not self.has_channel_permissions(guild, channel):
            raise RuntimeError(
                "active-lobby channel requires View Channel, Send Messages, "
                "Embed Links, Attach Files, Read Message History, and Mention Everyone"
            )

        initial_baseline = not stored and empty is None
        lobby_ids = [int(candidate["id"]) for candidate in candidates]
        await self.database.ensure_active_lobby_post_states(
            guild_id,
            lobby_ids,
            notification_handled=initial_baseline,
        )
        stored = await self.database.get_active_lobby_post_states(guild_id)
        stored_by_id = {int(row["lobby_id"]): row for row in stored}

        visible: list[tuple[dict[str, Any], LobbyPollState]] = []
        for candidate in candidates:
            state = self._states.setdefault(int(candidate["id"]), LobbyPollState())
            if result_is_resolved(candidate, state) and state.draft_finalized:
                continue
            visible.append((candidate, state))

        visible_ids = {int(candidate["id"]) for candidate, _ in visible}
        for row in stored:
            if int(row["lobby_id"]) not in visible_ids:
                await self._delete_lobby_post(guild, row)

        role = self._notification_role(guild, config.get("active_lobby_role_id"))
        for candidate, state in visible:
            lobby_id = int(candidate["id"])
            rendered = build_active_lobby_embed(
                candidate,
                state,
                emoji_lookup=self._emoji_lookup,
                map_resolver=self.map_resolver,
                battlevive_url=self.battlevive_url,
            )
            await self._reconcile_lobby_message(
                guild_id,
                channel,
                candidate,
                stored_by_id.get(lobby_id),
                rendered,
                role,
            )

        if visible:
            if empty is not None:
                await self._delete_empty_post(guild, empty)
        else:
            await self._reconcile_empty_post(guild_id, guild, channel, empty)

    async def _reconcile_lobby_message(
        self,
        guild_id: int,
        channel: Any,
        candidate: Mapping[str, Any],
        stored: Mapping[str, Any] | None,
        rendered: RenderedLobby,
        role: Any | None,
    ) -> None:
        lobby_id = int(candidate["id"])
        message = None
        same_channel = bool(
            stored
            and stored.get("channel_id") == channel.id
            and stored.get("message_id") is not None
        )
        if same_channel:
            try:
                message = await channel.fetch_message(int(stored["message_id"]))
            except discord.NotFound:
                message = None

        if message is not None:
            if stored.get("fingerprint") == rendered.fingerprint:
                return
            await self._edit_lobby_message(message, rendered)
            await self.database.record_active_lobby_post(
                guild_id,
                lobby_id,
                channel.id,
                message.id,
                rendered.fingerprint,
                notification_handled=bool(stored.get("notification_handled")),
            )
            return

        notify = bool(stored is not None and not stored.get("notification_handled"))
        content = f"<@&{role.id}>" if notify and role is not None else None
        message = await self._send_lobby_message(channel, rendered, content, role)
        await self.database.record_active_lobby_post(
            guild_id,
            lobby_id,
            channel.id,
            message.id,
            rendered.fingerprint,
            notification_handled=True,
        )

        if stored and stored.get("channel_id") and stored.get("message_id"):
            if (
                stored["channel_id"] != channel.id
                or stored["message_id"] != message.id
            ):
                await self._delete_message_reference(
                    self.bot.get_guild(guild_id),
                    int(stored["channel_id"]),
                    int(stored["message_id"]),
                )

    async def _send_lobby_message(
        self,
        channel: Any,
        rendered: RenderedLobby,
        content: str | None,
        role: Any | None,
    ) -> Any:
        file = rendered.file()
        kwargs: dict[str, Any] = {
            "content": content,
            "embed": rendered.embed,
            "allowed_mentions": (
                discord.AllowedMentions(
                    everyone=False,
                    users=False,
                    roles=[role],
                    replied_user=False,
                )
                if content is not None and role is not None
                else discord.AllowedMentions.none()
            ),
        }
        if file is not None:
            kwargs["file"] = file
        try:
            return await channel.send(**kwargs)
        finally:
            if file is not None:
                file.close()

    async def _edit_lobby_message(
        self,
        message: Any,
        rendered: RenderedLobby,
    ) -> None:
        file = rendered.file()
        attachments = [file] if file is not None else []
        try:
            await message.edit(
                embed=rendered.embed,
                attachments=attachments,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        finally:
            if file is not None:
                file.close()

    async def _reconcile_empty_post(
        self,
        guild_id: int,
        guild: Any,
        channel: Any,
        stored: Mapping[str, Any] | None,
    ) -> None:
        embed, fingerprint = build_empty_embed()
        message = None
        if (
            stored
            and stored.get("channel_id") == channel.id
            and stored.get("message_id") is not None
        ):
            try:
                message = await channel.fetch_message(int(stored["message_id"]))
            except discord.NotFound:
                message = None
        if message is not None:
            if stored.get("fingerprint") != fingerprint:
                await message.edit(
                    embed=embed,
                    attachments=[],
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                await self.database.record_active_lobby_empty_post(
                    guild_id, channel.id, message.id, fingerprint
                )
            return

        message = await channel.send(
            embed=embed,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        await self.database.record_active_lobby_empty_post(
            guild_id, channel.id, message.id, fingerprint
        )
        if stored and stored.get("channel_id") and stored.get("message_id"):
            await self._delete_message_reference(
                guild,
                int(stored["channel_id"]),
                int(stored["message_id"]),
            )

    async def _remove_all_posts(
        self,
        guild: Any,
        stored: Sequence[Mapping[str, Any]],
        empty: Mapping[str, Any] | None,
    ) -> None:
        for row in stored:
            await self._delete_lobby_post(guild, row)
        if empty is not None:
            await self._delete_empty_post(guild, empty)

    async def _delete_lobby_post(self, guild: Any, row: Mapping[str, Any]) -> None:
        channel_id = row.get("channel_id")
        message_id = row.get("message_id")
        if channel_id is None or message_id is None:
            return
        if await self._delete_message_reference(guild, int(channel_id), int(message_id)):
            await self.database.clear_active_lobby_post_message(
                int(row["guild_id"]),
                int(row["lobby_id"]),
                int(channel_id),
                int(message_id),
            )

    async def _delete_empty_post(self, guild: Any, row: Mapping[str, Any]) -> None:
        channel_id = row.get("channel_id")
        message_id = row.get("message_id")
        if channel_id is None or message_id is None:
            return
        if await self._delete_message_reference(guild, int(channel_id), int(message_id)):
            await self.database.clear_active_lobby_empty_post(
                int(row["guild_id"]),
                int(channel_id),
                int(message_id),
            )

    @staticmethod
    async def _delete_message_reference(
        guild: Any,
        channel_id: int,
        message_id: int,
    ) -> bool:
        channel = guild.get_channel(channel_id) if guild is not None else None
        if channel is None:
            return False
        try:
            message = await channel.fetch_message(message_id)
            await message.delete()
            return True
        except discord.NotFound:
            return True
        except discord.HTTPException:
            logger.warning(
                "Could not remove obsolete active-lobby message %s.",
                message_id,
                exc_info=True,
            )
            return False

    @staticmethod
    def _notification_role(guild: Any, role_id: object) -> Any | None:
        if role_id is None:
            return None
        role = guild.get_role(int(role_id))
        if role is None or getattr(role, "managed", False):
            return None
        is_default = getattr(role, "is_default", None)
        if callable(is_default) and is_default():
            return None
        return role

    @staticmethod
    def has_channel_permissions(guild: Any, channel: Any) -> bool:
        if guild.me is None:
            return False
        permissions = channel.permissions_for(guild.me)
        return all(
            (
                permissions.view_channel,
                permissions.send_messages,
                permissions.embed_links,
                permissions.attach_files,
                permissions.read_message_history,
                permissions.mention_everyone,
            )
        )

    async def _refresh_application_emojis(self) -> None:
        if self._emojis_loaded:
            return
        try:
            fetch = getattr(self.bot, "fetch_application_emojis", None)
            emojis = await fetch() if callable(fetch) else getattr(self.bot, "emojis", [])
            self._emoji_lookup = application_emoji_lookup(emojis)
            self._emojis_loaded = True
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "Could not refresh application emojis; retaining cached emoji lookup.",
                exc_info=True,
            )

    async def _worker(self) -> None:
        await self.bot.wait_until_ready()
        while not self._closed.is_set():
            self._requested.clear()
            try:
                await self.reconcile_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Active-lobby reconciliation pass failed.")
                self.polling_active = False

            timeout = self.poll_interval if self.polling_active else self.idle_interval
            try:
                await asyncio.wait_for(self._requested.wait(), timeout=timeout)
            except TimeoutError:
                pass

    async def _listen(self) -> None:
        if not self.dsn:
            await self._closed.wait()
            return
        backoff = 1.0
        while not self._closed.is_set():
            try:
                connection = await asyncpg.connect(dsn=self.dsn, command_timeout=10)
                self._listener_connection = connection
                await connection.add_listener(
                    ACTIVE_LOBBY_NOTIFY_CHANNEL,
                    self.request_reconciliation,
                )
                backoff = 1.0
                self.request_reconciliation()
                await self._closed.wait()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Active-lobby database listener disconnected.")
                try:
                    await asyncio.wait_for(self._closed.wait(), timeout=backoff)
                except TimeoutError:
                    pass
                backoff = min(backoff * 2, 30.0)
            finally:
                await self._close_listener()

    async def _close_listener(self) -> None:
        connection = self._listener_connection
        if connection is None:
            return
        self._listener_connection = None
        try:
            await connection.remove_listener(
                ACTIVE_LOBBY_NOTIFY_CHANNEL,
                self.request_reconciliation,
            )
        except Exception:
            logger.debug("Could not remove active-lobby listener cleanly.", exc_info=True)
        try:
            await connection.close()
        except Exception:
            logger.debug("Could not close active-lobby listener cleanly.", exc_info=True)

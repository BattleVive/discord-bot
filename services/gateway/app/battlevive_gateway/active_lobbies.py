"""Publish v1 active and disputed match state to configured Discord channels."""
from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
import re
from typing import Any

import discord

from .logs import logger


_MAX_TITLE = 256
_MAX_FIELD = 1_024
_MAX_DESCRIPTION = 4_096
_RUNTIME_ASSETS = Path("/app/assets")


def _text(value: object, default: str = "Unknown", *, limit: int = _MAX_FIELD) -> str:
    """Return trimmed display text constrained to Discord's field limits."""
    text = value.strip() if isinstance(value, str) and value.strip() else default
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _snowflake(value: object) -> int | None:
    """Parse a positive v1 Discord snowflake without accepting booleans."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.isdigit():
        parsed = int(value)
        return parsed if parsed > 0 else None
    return None


def _emoji_key(value: object) -> str:
    """Normalize v1 champion names to the corresponding Discord emoji key."""
    return re.sub(r"[^a-z0-9]+", "", str(value).casefold())


def _champion(value: object, emoji_lookup: Mapping[str, str]) -> str:
    """Use the configured v1 champion emoji when it is available."""
    name = _text(value, "No champion", limit=200)
    emoji = emoji_lookup.get(_emoji_key(name))
    return f"{emoji} {name}" if emoji else name


def _player_line(player: object, emoji_lookup: Mapping[str, str]) -> str:
    """Render one v1 roster member, preferring a safe exact Discord mention."""
    if not isinstance(player, dict):
        return "Unknown"
    discord_id = _snowflake(player.get("discord_id"))
    name = f"<@{discord_id}>" if discord_id is not None else _text(player.get("display_name"), "Unknown player", limit=300)
    return f"{name} · {_champion(player.get('champion_name'), emoji_lookup)}"


def _team_value(team: object, emoji_lookup: Mapping[str, str]) -> str:
    """Render one v1 team roster into one bounded embed field."""
    if not isinstance(team, dict):
        return "No roster data"
    players = team.get("players")
    if not isinstance(players, list) or not players:
        return "No players assigned"
    lines = [_player_line(player, emoji_lookup) for player in players]
    return _text("\n".join(lines), "No players assigned")


def _notification_content(config: dict[str, object], record: dict[str, object]) -> str | None:
    """Return the one applicable role mention for a newly created match post."""
    key = "website_moderator_role_id" if record.get("state") == "disputed" else "active_lobby_role_id"
    role_id = _snowflake(config.get(key))
    return f"<@&{role_id}>" if role_id is not None else None


def _draft_value(entries: object, emoji_lookup: Mapping[str, str]) -> str | None:
    """Render v1 pick or ban entries in draft-step order."""
    if not isinstance(entries, list):
        return None
    lines: list[str] = []
    for entry in sorted((item for item in entries if isinstance(item, dict)), key=lambda item: item.get("step", 0)):
        team = "Blue" if entry.get("team") == "team_one" else "Red" if entry.get("team") == "team_two" else "Unknown"
        champion = _champion(entry.get("champion_name"), emoji_lookup)
        lines.append(f"{team}: {champion}")
    return _text("\n".join(lines), limit=_MAX_FIELD) if lines else None


def _duration(seconds: object) -> str | None:
    """Format a valid non-negative v1 match duration."""
    if isinstance(seconds, bool) or not isinstance(seconds, int) or seconds < 0:
        return None
    minutes, remainder = divmod(seconds, 60)
    return f"{minutes}m {remainder}s" if minutes else f"{remainder}s"


class MapResolver:
    """Resolve selected v1 maps to their packaged day/night thumbnail files."""

    def __init__(self, assets_root: Path | None) -> None:
        self._assets_root = assets_root
        self._maps: dict[str, tuple[str, Path | None, Path | None]] = {}
        self._loaded = False

    def resolve(self, name: object, variant: object) -> tuple[str, Path | None]:
        """Return the display name and matching image file, if the manifest supplies one."""
        display = _text(name, "Unknown map", limit=300)
        if self._assets_root is None:
            return display, None
        self._load()
        entry = self._maps.get(_emoji_key(display))
        if entry is None:
            return display, None
        resolved_name, day, night = entry
        return resolved_name, night if variant == "night" else day

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            payload = json.loads((self._assets_root / "maps" / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            logger.warning("Active-lobby map manifest is unavailable; map thumbnails disabled.")
            return
        entries = payload.get("maps") if isinstance(payload, dict) else None
        if not isinstance(entries, list):
            return
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
                continue
            name = entry["name"].strip()
            if not name:
                continue
            day = self._map_path(entry.get("day"))
            night = self._map_path(entry.get("night"))
            for alias in [name, *(entry.get("aliases") if isinstance(entry.get("aliases"), list) else [])]:
                key = _emoji_key(alias)
                if key:
                    self._maps[key] = (name, day, night)

    def _map_path(self, value: object) -> Path | None:
        if not isinstance(value, str) or not value:
            return None
        path = self._assets_root / "maps" / value
        return path if path.is_file() else None


def _match_embed(record: dict[str, object], emoji_lookup: Mapping[str, str], map_resolver: MapResolver) -> tuple[discord.Embed, str, Path | None, str | None]:
    """Build a rich Discord embed directly from one normalized v1 match."""
    match_id = record["match_id"]
    state = _text(record.get("state"), "active", limit=100).replace("_", " ").title()
    if state.casefold() == "disputed":
        state = "⚠️ Disputed"
    size = record.get("size")
    size_text = f"{size}v{size}" if isinstance(size, int) and not isinstance(size, bool) and size > 0 else "Unknown size"
    description = " · ".join((state, _text(record.get("type"), limit=100).title(), size_text, _text(record.get("region"), limit=100).upper()))
    embed = discord.Embed(title=_text(record.get("title"), f"Match #{match_id}", limit=_MAX_TITLE), description=description[:_MAX_DESCRIPTION], colour=discord.Colour.orange() if state.casefold() == "⚠️ disputed" else discord.Colour.blurple())
    url = record.get("url")
    if isinstance(url, str) and url.startswith("https://"):
        embed.url = url
    one, two = record.get("team_one"), record.get("team_two")
    embed.add_field(name=_text(one.get("name") if isinstance(one, dict) else None, "Blue", limit=256), value=_team_value(one, emoji_lookup), inline=True)
    embed.add_field(name=_text(two.get("name") if isinstance(two, dict) else None, "Red", limit=256), value=_team_value(two, emoji_lookup), inline=True)
    draft = record.get("draft")
    if isinstance(draft, dict) and not draft.get("picks_hidden"):
        picks, bans = _draft_value(draft.get("picks"), emoji_lookup), _draft_value(draft.get("bans"), emoji_lookup)
        if picks:
            embed.add_field(name="Picks", value=picks, inline=False)
        if bans:
            embed.add_field(name="Bans", value=bans, inline=False)
    selected_map = record.get("selected_map")
    map_path, attachment_name = None, None
    if isinstance(selected_map, dict):
        name, map_path = map_resolver.resolve(selected_map.get("map_name"), selected_map.get("variant"))
        variant = selected_map.get("variant")
        embed.add_field(name="Map", value=f"{name} ({variant})" if variant in {"day", "night"} else name, inline=True)
        if map_path is not None:
            attachment_name = f"map-{map_path.name}"
            embed.set_thumbnail(url=f"attachment://{attachment_name}")
    winner = record.get("winner_team")
    if winner in {"team_one", "team_two"}:
        winning_team = one if winner == "team_one" else two
        embed.add_field(name="Winner", value=_text(winning_team.get("name") if isinstance(winning_team, dict) else None, str(winner).replace("_", " ")), inline=True)
    duration = _duration(record.get("duration_seconds"))
    if duration is not None:
        embed.add_field(name="Duration", value=duration, inline=True)
    fingerprint = hashlib.sha256(json.dumps(embed.to_dict(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return embed, fingerprint, map_path, attachment_name


def _empty_embed() -> tuple[discord.Embed, str]:
    """Build the original v1 empty active-match status card."""
    embed = discord.Embed(
        title="Active Matches",
        description="There are no active matches right now.",
        colour=discord.Colour.dark_grey(),
    )
    fingerprint = hashlib.sha256(json.dumps(embed.to_dict(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return embed, fingerprint


def _eligible(records: object, *, disputed_only: bool) -> list[dict[str, object]]:
    """Keep valid active records or valid disputed records from a v1 collection."""
    if not isinstance(records, list):
        raise RuntimeError("upstream match collection was invalid")
    result: list[dict[str, object]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        match_id = record.get("match_id")
        if isinstance(match_id, bool) or not isinstance(match_id, int) or match_id <= 0:
            continue
        if disputed_only and record.get("state") != "disputed":
            continue
        result.append(record)
    return result


class ActiveLobbyPublisher:
    """Reconcile v1 match publications into one guild channel."""

    def __init__(self, bot: discord.Client, upstream: Any, publications: Any, *,
                 assets_root: Path | None = None, emoji_lookup: Mapping[str, str] | None = None) -> None:
        self._bot, self._upstream, self._publications = bot, upstream, publications
        self._map_resolver = MapResolver(assets_root)
        self._emoji_lookup = emoji_lookup or {}

    async def reconcile_guild(self, config: dict[str, object]) -> bool:
        guild_id, channel_id = config.get("guild_id"), config.get("active_lobby_channel_id")
        if not isinstance(guild_id, int) or not isinstance(channel_id, int):
            return False
        guild = self._bot.get_guild(guild_id)
        if guild is None:
            return False
        channel = guild.get_channel(channel_id)
        if channel is None:
            raise RuntimeError("configured active-lobby channel is unavailable")
        active, disputed = await asyncio.gather(
            self._upstream.get_result("/active-matches", require_fresh=True),
            self._upstream.get_result("/disputed-matches", require_fresh=True),
        )
        matches = {int(record["match_id"]): record for record in _eligible(active.data.get("matches"), disputed_only=False)}
        for record in _eligible(disputed.data.get("matches"), disputed_only=True):
            matches.setdefault(int(record["match_id"]), record)
        existing = {str(row["publication_key"]): row for row in await self._publications.list_for_feature(guild_id, "active-lobbies")}
        changed = False
        empty = existing.pop("empty", None)
        if matches and empty is not None:
            empty_channel = guild.get_channel(empty.get("channel_id")) if isinstance(empty.get("channel_id"), int) else None
            if empty_channel is not None:
                await self._delete_message(empty_channel, empty)
            await self._publications.delete(guild_id, "active-lobbies", "empty")
            changed = True
        for match_id, record in matches.items():
            key = f"match:{match_id}"
            embed, fingerprint, map_path, attachment_name = _match_embed(record, self._emoji_lookup, self._map_resolver)
            previous = existing.pop(key, None)
            if previous is not None and previous.get("fingerprint") == fingerprint and previous.get("channel_id") == channel_id:
                continue
            if previous is not None and previous.get("channel_id") != channel_id:
                prior_channel = guild.get_channel(previous.get("channel_id")) if isinstance(previous.get("channel_id"), int) else None
                if prior_channel is not None:
                    await self._delete_message(prior_channel, previous)
                previous = None
            message = await self._existing_message(channel, previous)
            created = message is None
            if message is None:
                content = _notification_content(config, record)
                send_kwargs: dict[str, object] = {"embed": embed, "allowed_mentions": discord.AllowedMentions.none()}
                if content is not None:
                    # Explicit role-only mentions prevent roster display names
                    # or untrusted upstream data from triggering notifications.
                    send_kwargs.update({"content": content, "allowed_mentions": discord.AllowedMentions(everyone=False, users=False, roles=True, replied_user=False)})
                message = await self._send_message(channel, send_kwargs, map_path, attachment_name)
            else:
                await self._edit_message(message, embed, map_path, attachment_name)
            try:
                await self._publications.upsert(guild_id, "active-lobbies", key, channel_id, int(message.id), None, fingerprint, {"source": "api/v1/matches", "state": _text(record.get("state"))})
            except Exception:
                if created:
                    try:
                        await message.delete()
                    except discord.NotFound:
                        pass
                raise
            changed = True
        for key, previous in existing.items():
            prior_channel_id = previous.get("channel_id")
            prior_channel = guild.get_channel(prior_channel_id) if isinstance(prior_channel_id, int) else None
            if prior_channel is not None:
                await self._delete_message(prior_channel, previous)
            await self._publications.delete(guild_id, "active-lobbies", key)
            changed = True
        if not matches:
            embed, fingerprint = _empty_embed()
            if empty is not None and empty.get("channel_id") == channel_id:
                message = await self._existing_message(channel, empty)
                if message is not None:
                    if empty.get("fingerprint") == fingerprint:
                        return changed
                    await message.edit(content=None, embed=embed, allowed_mentions=discord.AllowedMentions.none())
                    await self._publications.upsert(
                        guild_id, "active-lobbies", "empty", channel_id, int(message.id), None,
                        fingerprint, {"source": "api/v1/matches", "state": "empty"},
                    )
                    return True
            if empty is not None:
                prior_channel = guild.get_channel(empty.get("channel_id")) if isinstance(empty.get("channel_id"), int) else None
                if prior_channel is not None:
                    await self._delete_message(prior_channel, empty)
            message = await channel.send(content=None, embed=embed, allowed_mentions=discord.AllowedMentions.none())
            try:
                await self._publications.upsert(
                    guild_id, "active-lobbies", "empty", channel_id, int(message.id), None,
                    fingerprint, {"source": "api/v1/matches", "state": "empty"},
                )
            except Exception:
                try:
                    await message.delete()
                except discord.NotFound:
                    pass
                raise
            changed = True
        return changed

    @staticmethod
    async def _send_message(channel: Any, kwargs: dict[str, object], map_path: Path | None, attachment_name: str | None) -> Any:
        """Send a match and close an optional map attachment after Discord consumes it."""
        file = discord.File(map_path, filename=attachment_name) if map_path is not None and attachment_name is not None else None
        if file is not None:
            kwargs["file"] = file
        try:
            return await channel.send(**kwargs)
        finally:
            if file is not None:
                file.close()

    @staticmethod
    async def _edit_message(message: Any, embed: discord.Embed, map_path: Path | None, attachment_name: str | None) -> None:
        """Update a match without letting rendered roster mentions notify members."""
        file = discord.File(map_path, filename=attachment_name) if map_path is not None and attachment_name is not None else None
        kwargs: dict[str, object] = {
            "embed": embed,
            "attachments": [file] if file is not None else [],
            "allowed_mentions": discord.AllowedMentions.none(),
        }
        try:
            await message.edit(**kwargs)
        finally:
            if file is not None:
                file.close()

    @staticmethod
    async def _existing_message(channel: Any, publication: dict[str, object] | None) -> Any | None:
        if publication is None or not isinstance(publication.get("message_id"), int):
            return None
        try:
            return await channel.fetch_message(publication["message_id"])
        except discord.NotFound:
            return None

    @staticmethod
    async def _delete_message(channel: Any, publication: dict[str, object]) -> None:
        message = await ActiveLobbyPublisher._existing_message(channel, publication)
        if message is not None:
            try:
                await message.delete()
            except discord.NotFound:
                pass


class ActiveLobbyService:
    """Periodic best-effort active-lobby reconciliation."""

    def __init__(self, bot: discord.Client, pool: Any, upstream: Any, *, interval: float = 15.0,
                 assets_root: Path | None = _RUNTIME_ASSETS) -> None:
        self._bot, self._pool, self._upstream, self._interval = bot, pool, upstream, interval
        self._assets_root = assets_root
        self._requested = asyncio.Event()
        self._reconcile_lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="active-lobby-publisher")
            self.request_reconciliation()

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    def request_reconciliation(self) -> None:
        self._requested.set()

    async def reconcile_all(self) -> None:
        from .repositories import GuildConfigRepository, PublicationRepository
        from .guides import application_emoji_lookup
        async with self._reconcile_lock:
            emojis = await application_emoji_lookup(self._bot)
            async with self._pool.acquire() as connection:
                configs = await GuildConfigRepository(connection).configured_active_lobbies()
                publisher = ActiveLobbyPublisher(self._bot, self._upstream, PublicationRepository(connection),
                                                 assets_root=self._assets_root, emoji_lookup=emojis)
                for config in configs:
                    try:
                        await publisher.reconcile_guild(config)
                    except Exception:
                        logger.exception("Active-lobby reconciliation failed for guild %s", config.get("guild_id"))

    async def _run(self) -> None:
        while True:
            try:
                await asyncio.wait_for(self._requested.wait(), timeout=self._interval)
            except TimeoutError:
                pass
            self._requested.clear()
            try:
                await self.reconcile_all()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Active-lobby reconciliation pass failed")

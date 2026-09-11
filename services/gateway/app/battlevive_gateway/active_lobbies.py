"""Automatic publication of active and disputed BattleVive matches."""
from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

import discord

from .logs import logger


_MAX_TITLE = 256
_MAX_DESCRIPTION_PART = 100
_MAX_FIELD_VALUE = 1_024
_MAX_OPTIONAL_FIELD_VALUE = 512


def _text(record: dict[str, object], key: str, default: str = "Unknown", *, limit: int = _MAX_FIELD_VALUE) -> str:
    """Return a normalized text value."""
    value = record.get(key)
    text = value.strip() if isinstance(value, str) and value.strip() else default
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _duration(seconds: object) -> str | None:
    """Format a match duration for display."""
    if isinstance(seconds, bool) or not isinstance(seconds, int) or seconds < 0:
        return None
    minutes, remainder = divmod(seconds, 60)
    return f"{minutes}m {remainder}s" if minutes else f"{remainder}s"


def _match_embed(record: dict[str, object]) -> tuple[discord.Embed, str]:
    """Build the Discord embed for a match."""
    status = _text(record, "status", "active", limit=_MAX_DESCRIPTION_PART).replace("_", " ").title()
    if status.casefold() == "disputed":
        status = "⚠️ Disputed"
    size = record.get("size")
    size_text = f"{size}v{size}" if isinstance(size, int) and not isinstance(size, bool) and size > 0 else "Unknown size"
    embed = discord.Embed(
        title=_text(record, "title", f"Match #{record['id']}", limit=_MAX_TITLE),
        description=" · ".join((status, _text(record, "type", limit=_MAX_DESCRIPTION_PART).title(), size_text, _text(record, "region", limit=_MAX_DESCRIPTION_PART).upper())),
        colour=discord.Colour.orange() if status.casefold() == "⚠️ disputed" else discord.Colour.blurple(),
    )
    url = record.get("url")
    if isinstance(url, str) and url.startswith("https://"):
        embed.url = url
    embed.add_field(name="Team One", value=_text(record, "teamOne"), inline=True)
    embed.add_field(name="Team Two", value=_text(record, "teamTwo"), inline=True)
    winner = record.get("winner")
    if isinstance(winner, str) and winner.strip():
        embed.add_field(name="Winner", value=_text(record, "winner", limit=_MAX_OPTIONAL_FIELD_VALUE), inline=True)
    duration = _duration(record.get("durationSeconds"))
    if duration is not None:
        embed.add_field(name="Duration", value=duration, inline=True)
    fingerprint = hashlib.sha256(json.dumps(embed.to_dict(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return embed, fingerprint


def _eligible(records: object, *, disputed_only: bool) -> list[dict[str, object]]:
    """Return whether a match is eligible for publication."""
    if not isinstance(records, list):
        raise RuntimeError("upstream match collection was invalid")
    result: list[dict[str, object]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        match_id = record.get("id")
        if isinstance(match_id, bool) or not isinstance(match_id, int) or match_id <= 0:
            continue
        if disputed_only and _text(record, "status").casefold() != "disputed":
            continue
        result.append(record)
    return result


class ActiveLobbyPublisher:
    """Reconcile active matches and unresolved disputes into one guild channel."""

    def __init__(self, bot: discord.Client, upstream: Any, publications: Any) -> None:
        """Initialize the active lobby publisher instance."""
        self._bot, self._upstream, self._publications = bot, upstream, publications

    async def reconcile_guild(self, config: dict[str, object]) -> bool:
        """Reconcile one guild's active-lobby publications."""
        guild_id, channel_id = config.get("guild_id"), config.get("active_lobby_channel_id")
        if not isinstance(guild_id, int) or not isinstance(channel_id, int):
            return False
        guild = self._bot.get_guild(guild_id)
        if guild is None:
            return False
        channel = guild.get_channel(channel_id)
        if channel is None:
            raise RuntimeError("configured active-lobby channel is unavailable")
        active, recent = await asyncio.gather(
            self._upstream.get_result("/active-matches", require_fresh=True),
            self._upstream.get_result("/recent-matches", require_fresh=True),
        )
        matches = {int(record["id"]): record for record in _eligible(active.data.get("matches"), disputed_only=False)}
        for record in _eligible(recent.data.get("matches"), disputed_only=True):
            matches.setdefault(int(record["id"]), record)
        existing = {str(row["publication_key"]): row for row in await self._publications.list_for_feature(guild_id, "active-lobbies")}
        changed = False
        for match_id, record in matches.items():
            key = f"match:{match_id}"
            embed, fingerprint = _match_embed(record)
            previous = existing.pop(key, None)
            if previous is not None and previous.get("fingerprint") == fingerprint and previous.get("channel_id") == channel_id:
                continue
            if previous is not None and previous.get("channel_id") != channel_id:
                previous_channel_id = previous.get("channel_id")
                old_channel = guild.get_channel(previous_channel_id) if isinstance(previous_channel_id, int) else None
                if old_channel is not None:
                    await self._delete_message(old_channel, previous)
                previous = None
            message = await self._existing_message(channel, previous)
            created = message is None
            if message is None:
                message = await channel.send(embed=embed)
            else:
                await message.edit(embed=embed)
            try:
                await self._publications.upsert(guild_id, "active-lobbies", key, channel_id, int(message.id), None, fingerprint,
                                                {"source": "api/bot/matches/active", "status": _text(record, "status")})
            except Exception:
                if created:
                    try:
                        await message.delete()
                    except discord.NotFound:
                        pass
                raise
            changed = True
        for key, previous in existing.items():
            await self._delete_message(channel, previous)
            await self._publications.delete(guild_id, "active-lobbies", key)
            changed = True
        return changed

    @staticmethod
    async def _existing_message(channel: Any, publication: dict[str, object] | None) -> Any | None:
        """Fetch a tracked Discord message when it still exists."""
        if publication is None or not isinstance(publication.get("message_id"), int):
            return None
        try:
            return await channel.fetch_message(publication["message_id"])
        except discord.NotFound:
            return None

    @staticmethod
    async def _delete_message(channel: Any, publication: dict[str, object]) -> None:
        """Delete message."""
        message = await ActiveLobbyPublisher._existing_message(channel, publication)
        if message is not None:
            try:
                await message.delete()
            except discord.NotFound:
                pass


class ActiveLobbyService:
    """Periodic best-effort active-lobby reconciliation."""

    def __init__(self, bot: discord.Client, pool: Any, upstream: Any, *, interval: float = 15.0) -> None:
        """Initialize the active lobby service instance."""
        self._bot, self._pool, self._upstream, self._interval = bot, pool, upstream, interval
        self._requested = asyncio.Event()
        self._reconcile_lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """Start the periodic reconciliation worker."""
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="active-lobby-publisher")
            self.request_reconciliation()

    async def stop(self) -> None:
        """Stop the periodic reconciliation worker."""
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    def request_reconciliation(self) -> None:
        """Wake the worker for an immediate reconciliation pass."""
        self._requested.set()

    async def reconcile_all(self) -> None:
        """Reconcile active lobbies for every configured guild."""
        from .repositories import GuildConfigRepository, PublicationRepository
        async with self._reconcile_lock:
            async with self._pool.acquire() as connection:
                configs = await GuildConfigRepository(connection).configured_active_lobbies()
                publisher = ActiveLobbyPublisher(self._bot, self._upstream, PublicationRepository(connection))
                for config in configs:
                    try:
                        await publisher.reconcile_guild(config)
                    except Exception:
                        logger.exception("Active-lobby reconciliation failed for guild %s", config.get("guild_id"))

    async def _run(self) -> None:
        """Run reconciliation until the service is stopped."""
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

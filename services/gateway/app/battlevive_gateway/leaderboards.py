"""Automatic configured-channel leaderboard publication."""
from __future__ import annotations

import asyncio
import hashlib
from io import BytesIO
import json
from typing import Any

import discord

from .logs import logger
from .identity import save_guild_identity_links


def _win_rate(wins: int, losses: int) -> int:
    return round(wins * 100 / (wins + losses)) if wins + losses else 0


def _entry(record: dict[str, Any], place: int) -> dict[str, object] | None:
    player, rank = record.get("player"), record.get("rank")
    mmr, wins, losses = record.get("mmr"), record.get("wins"), record.get("losses")
    if not isinstance(player, str) or not isinstance(rank, str):
        return None
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in (mmr, wins, losses)):
        return None
    return {"place": place, "username": player, "rank": rank, "mmr": mmr,
            "wins": wins, "losses": losses, "win_rate": _win_rate(wins, losses)}


class GuildLeaderboardPublisher:
    """Use the renderer HTTP service and persistent publication state, never a shared volume."""

    def __init__(self, bot: discord.Client, upstream: Any, renderer: Any, publications: Any, identities: Any | None = None) -> None:
        self._bot = bot
        self._upstream = upstream
        self._renderer = renderer
        self._publications = publications
        self._identities = identities

    async def reconcile_guild(self, config: dict[str, object]) -> bool:
        guild_id, channel_id = config.get("guild_id"), config.get("leaderboard_channel_id")
        if not isinstance(guild_id, int) or not isinstance(channel_id, int):
            return False
        guild = self._bot.get_guild(guild_id)
        if guild is None:
            # setup_hook runs before Discord has necessarily populated the
            # guild cache.  The periodic reconciler will retry after ready.
            return False
        channel = guild.get_channel(channel_id)
        if channel is None:
            raise RuntimeError("configured leaderboard channel is unavailable")
        result = await self._upstream.get_result("/leaderboard", require_fresh=True)
        records = result.data.get("leaderboard")
        if not isinstance(records, list):
            raise RuntimeError("upstream leaderboard was invalid")
        if self._identities is not None:
            await save_guild_identity_links(guild, records, self._identities)
            member_numbers = [record.get("member_number") for record in records if isinstance(record, dict)]
            known = await self._identities.discord_ids([number for number in member_numbers if isinstance(number, int) and not isinstance(number, bool)])
            guild_members = {int(member.id) for member in getattr(guild, "members", ())}
            records = [record for record in records if isinstance(record, dict)
                       and known.get(record.get("member_number")) in guild_members]
        limit = config.get("leaderboard_limit", 10)
        if isinstance(limit, bool) or not isinstance(limit, int):
            limit = 10
        entries = [entry for place, record in enumerate(records[:limit], start=1)
                   if isinstance(record, dict) and (entry := _entry(record, place)) is not None]
        season = result.data.get("season")
        model: dict[str, object] = {"season": season if isinstance(season, str) else "Current season", "entries": entries}
        fingerprint = hashlib.sha256(json.dumps(model, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        existing = {str(row["publication_key"]): row for row in await self._publications.list_for_feature(guild_id, "leaderboard")}
        previous = existing.get("slot:0")
        if previous is not None and previous.get("fingerprint") == fingerprint and previous.get("channel_id") == channel_id:
            return False
        png = await self._renderer.render_leaderboard(model)
        message = await self._existing_message(channel, previous)
        file = discord.File(BytesIO(png), filename="leaderboard.png")
        if message is None:
            message = await channel.send(file=file)
        else:
            await message.edit(attachments=[file])
        await self._publications.upsert(guild_id, "leaderboard", "slot:0", channel_id,
                                        int(message.id), None, fingerprint,
                                        {"season": model["season"], "entry_count": len(entries), "source": "api/bot/leaderboard",
                                         "source_may_be_capped": True})
        return True

    @staticmethod
    async def _existing_message(channel: Any, publication: dict[str, object] | None) -> Any | None:
        if publication is None or not isinstance(publication.get("message_id"), int):
            return None
        try:
            return await channel.fetch_message(publication["message_id"])
        except discord.NotFound:
            return None


class LeaderboardService:
    """Periodic best-effort automatic reconciliation; private service loss is non-fatal."""

    def __init__(self, bot: discord.Client, pool: Any, upstream: Any, renderer: Any, *, interval: float = 60.0) -> None:
        self._bot, self._pool, self._upstream, self._renderer, self._interval = bot, pool, upstream, renderer, interval
        self._requested = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="leaderboard-publisher")
            self.request_reconciliation()

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    def request_reconciliation(self) -> None:
        self._requested.set()

    async def reconcile_all(self) -> None:
        from .repositories import GuildConfigRepository, IdentityRepository, PublicationRepository
        async with self._pool.acquire() as connection:
            configs = await GuildConfigRepository(connection).configured_leaderboards()
            publisher = GuildLeaderboardPublisher(
                self._bot, self._upstream, self._renderer, PublicationRepository(connection), IdentityRepository(connection)
            )
            for config in configs:
                try:
                    await publisher.reconcile_guild(config)
                except Exception:
                    logger.exception("Leaderboard reconciliation failed for guild %s", config.get("guild_id"))

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
                logger.exception("Leaderboard reconciliation pass failed")

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from datetime import datetime
from typing import Any

import discord

from .battlevive.guides import GuideCatalogSource
from .battlevive.guides import GuideContentSource
from .battlevive.guides import GuideMetadata
from .logs import logger


GUIDE_MARKER_PREFIX = "\u2063battlevive-guide:"
GUIDE_MARKER_SUFFIX = "\u2063"
DISCORD_MESSAGE_LIMIT = 2_000


def guide_marker(source_id: str) -> str:
    return f"{GUIDE_MARKER_PREFIX}{source_id}{GUIDE_MARKER_SUFFIX}"


def split_markdown(markdown: str, *, limit: int = DISCORD_MESSAGE_LIMIT) -> list[str]:
    """Split in-memory Markdown without losing content or exceeding Discord limits."""
    if limit < 2:
        raise ValueError("message limit must permit content")
    if not markdown:
        return [""]
    chunks: list[str] = []
    remaining = markdown
    while len(remaining) > limit:
        boundary = max(remaining.rfind("\n\n", 0, limit + 1), remaining.rfind("\n", 0, limit + 1))
        if boundary <= 0:
            boundary = limit
        else:
            boundary += 2 if remaining.startswith("\n\n", boundary) else 1
        chunks.append(remaining[:boundary])
        remaining = remaining[boundary:]
    chunks.append(remaining)
    return chunks


class GuideThreadService:
    """Reconcile public guide metadata with managed Discord forum posts."""

    def __init__(
        self,
        bot: Any,
        database: Any,
        catalog: GuideCatalogSource,
        content: GuideContentSource,
        *,
        reconcile_interval: float = 300,
    ) -> None:
        self.bot = bot
        self.database = database
        self.catalog = catalog
        self.content = content
        self.reconcile_interval = reconcile_interval
        self._requested = asyncio.Event()
        self._closed = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []
        self._lock = asyncio.Lock()

    def start(self) -> None:
        if self._tasks:
            return
        self._closed.clear()
        self._tasks = [
            asyncio.create_task(self._worker(), name="guide-thread-worker"),
            asyncio.create_task(self._periodic(), name="guide-thread-periodic"),
        ]
        self.request_reconciliation()

    async def stop(self) -> None:
        self._closed.set()
        self._requested.set()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    def is_running(self) -> bool:
        return bool(self._tasks) and not self._closed.is_set() and all(not task.done() for task in self._tasks)

    def request_reconciliation(self, *_args: object) -> None:
        self._requested.set()

    async def _worker(self) -> None:
        while not self._closed.is_set():
            await self._requested.wait()
            self._requested.clear()
            try:
                await self.bot.wait_until_ready()
                await self.reconcile_all()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Guide reconciliation pass failed.")

    async def _periodic(self) -> None:
        while not self._closed.is_set():
            try:
                await asyncio.wait_for(self._closed.wait(), timeout=self.reconcile_interval)
            except TimeoutError:
                self.request_reconciliation()

    async def reconcile_all(self) -> None:
        async with self._lock:
            guides = await self.catalog.list_guides()
            for config in await self.database.get_configured_guides():
                try:
                    await self.reconcile_guild(config, guides)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Guide reconciliation failed for guild %s.", config["guild_id"])

    async def reconcile_guild(self, config: dict[str, Any], guides: list[GuideMetadata]) -> None:
        guild_id = config["guild_id"]
        tracked = {row["source_guide_id"]: row for row in await self.database.get_guide_threads(guild_id)}
        channel_id = config.get("guide_forum_channel_id")
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise RuntimeError("configured guild is not available")
        channel = guild.get_channel(channel_id) if channel_id else None
        if channel_id is not None and (not isinstance(channel, discord.ForumChannel) or not self._has_permissions(guild, channel)):
            raise RuntimeError("guide forum requires View Channel, Send Messages, Read Message History, Manage Threads, and Mention Everyone")

        current_ids = {guide.source_id for guide in guides}
        for source_id, row in tracked.items():
            if source_id not in current_ids:
                await self._remove_missing(guild, row, bool(config.get("guide_auto_delete_on_removal")))

        if channel is None:
            return
        for guide in guides:
            row = tracked.get(guide.source_id)
            if row is None:
                await self._publish(channel, guild, config, guide)
                continue
            thread = guild.get_thread(row["thread_id"])
            if thread is None:
                try:
                    thread = await self.bot.fetch_channel(row["thread_id"])
                except discord.NotFound:
                    await self._publish(channel, guild, config, guide)
                    continue
            if not isinstance(thread, discord.Thread):
                await self._publish(channel, guild, config, guide)
                continue
            if row["source_updated_at"] < guide.last_modified or thread.parent_id != channel.id:
                if thread.parent_id != channel.id:
                    await thread.edit(archived=True, locked=False, reason="Guide forum channel changed")
                    await self._publish(channel, guild, config, guide)
                else:
                    await self._replace(thread, guide)
                    await self.database.upsert_guide_thread(guild_id, guide.source_id, thread.id, guide.last_modified)

    @staticmethod
    def _has_permissions(guild: discord.Guild, channel: discord.ForumChannel) -> bool:
        member = guild.me
        if member is None:
            return False
        permissions = channel.permissions_for(member)
        return all((permissions.view_channel, permissions.send_messages, permissions.read_message_history, permissions.manage_threads, permissions.mention_everyone))

    async def _publish(self, channel: discord.ForumChannel, guild: discord.Guild, config: dict[str, Any], guide: GuideMetadata) -> None:
        markdown = await self.content.fetch_markdown(guide.source_id)
        marker = guide_marker(guide.source_id)
        chunks = split_markdown(markdown, limit=DISCORD_MESSAGE_LIMIT - len(marker))
        role = guild.get_role(config["guide_notification_role_id"]) if config.get("guide_notification_role_id") else None
        prefix = role.mention + "\n" if role is not None else ""
        thread_with_message = await channel.create_thread(
            name=guide.title[:100],
            content=prefix + chunks[0] + marker,
            allowed_mentions=discord.AllowedMentions(roles=[role] if role else [], everyone=False, users=False),
        )
        thread = thread_with_message.thread
        for chunk in chunks[1:]:
            await thread.send(chunk + marker, allowed_mentions=discord.AllowedMentions.none())
        await self.database.upsert_guide_thread(config["guild_id"], guide.source_id, thread.id, guide.last_modified)

    async def _replace(self, thread: discord.Thread, guide: GuideMetadata) -> None:
        marker = guide_marker(guide.source_id)
        messages = [message async for message in thread.history(limit=None, oldest_first=True)]
        managed = [message for message in messages if marker in message.content]
        markdown = await self.content.fetch_markdown(guide.source_id)
        chunks = split_markdown(markdown, limit=DISCORD_MESSAGE_LIMIT - len(marker))
        if managed:
            await managed[0].edit(content=chunks[0] + marker, allowed_mentions=discord.AllowedMentions.none())
            for message, chunk in zip(managed[1:], chunks[1:]):
                await message.edit(content=chunk + marker, allowed_mentions=discord.AllowedMentions.none())
            for message in managed[len(chunks):]:
                await message.delete()
            for chunk in chunks[len(managed):]:
                await thread.send(chunk + marker, allowed_mentions=discord.AllowedMentions.none())
        else:
            for chunk in chunks:
                await thread.send(chunk + marker, allowed_mentions=discord.AllowedMentions.none())
        await thread.edit(name=guide.title[:100])

    async def _remove_missing(self, guild: discord.Guild, row: dict[str, Any], delete: bool) -> None:
        try:
            thread = guild.get_thread(row["thread_id"]) or await self.bot.fetch_channel(row["thread_id"])
            if delete:
                await thread.delete(reason="Guide removed from Battlevive")
            else:
                await thread.edit(archived=True, locked=False, reason="Guide removed from Battlevive")
        except discord.NotFound:
            pass
        else:
            pass
        await self.database.remove_guide_thread(row["guild_id"], row["source_guide_id"])

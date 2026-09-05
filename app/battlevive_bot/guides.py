from __future__ import annotations

import asyncio
from collections.abc import Iterable
from collections.abc import Mapping
import re
from typing import Any
from urllib.parse import quote
from urllib.parse import urlparse

import discord

from .battlevive.guides import GuideCatalogSource
from .battlevive.guides import GuideContentSource
from .battlevive.guides import GuideMetadata
from .logs import logger


DISCORD_MESSAGE_LIMIT = 2_000
CHAMPION_ICON_BASE_URL = "https://battlevive.com/images/champions/icons"
CHAMPION_ICON_ASSET_NAMES = {"Shen Rao": "Shen-Rao"}
_MARKDOWN_IMAGE = re.compile(r"!\[[^\]]*\]\((<?[^)\s>]+>?)(?:\s+[^)]*)?\)")
_GUIDE_IMAGE_URL = re.compile(r"https://battlevive\.com/images/champions/(?:icons|battlerites)/[^\s<>()]+\.png")
_HORIZONTAL_RULE = re.compile(r"(?m)^[ \t]*(?:-{3,}|\*{3,}|_{3,})[ \t]*$")
_FENCED_CODE = re.compile(r"^[ \t]*(`{3,}|~{3,})")
_INLINE_CODE = re.compile(r"(`+[^`\n]*`+)")


def normalize_discord_markdown(markdown: str, *, emoji_lookup: Mapping[str, str] | None = None) -> str:
    """Keep Discord-supported Markdown and translate unsupported GFM syntax."""
    emoji_lookup = emoji_lookup or {}
    lines = markdown.split("\n")
    normalized: list[str] = []
    in_fenced_code = False
    skip_following_blank = False
    for index, line in enumerate(lines):
        if _FENCED_CODE.match(line):
            in_fenced_code = not in_fenced_code
            normalized.append(line)
            continue
        if not in_fenced_code and _HORIZONTAL_RULE.fullmatch(line):
            skip_following_blank = bool(normalized and not normalized[-1] and index + 1 < len(lines) and not lines[index + 1])
            continue
        if skip_following_blank and not line:
            skip_following_blank = False
            continue
        skip_following_blank = False
        normalized.append(line if in_fenced_code else _normalize_inline_images(line, emoji_lookup))
    return "\n".join(normalized).strip()


def _normalize_inline_images(line: str, emoji_lookup: Mapping[str, str]) -> str:
    """Convert image Markdown outside inline code to raw URLs for Discord previews."""
    return "".join(
        segment if _INLINE_CODE.fullmatch(segment) else _rewrite_image_urls(segment, emoji_lookup)
        for segment in _INLINE_CODE.split(line)
    )


def _rewrite_image_urls(segment: str, emoji_lookup: Mapping[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        following = segment[match.end()] if match.end() < len(segment) else ""
        separator = " " if following and not following.isspace() and following != "!" else ""
        return _emoji_for_url(match.group(1).strip("<>"), emoji_lookup) + separator

    return _GUIDE_IMAGE_URL.sub(
        lambda match: _emoji_for_url(match.group(), emoji_lookup),
        _MARKDOWN_IMAGE.sub(replace, segment),
    )


def _emoji_for_url(url: str, emoji_lookup: Mapping[str, str]) -> str:
    """Return an application emoji mention, or its readable :name: fallback."""
    key = _emoji_key_from_url(url)
    return emoji_lookup.get(key, f":{key}:") if key else url


def _emoji_key_from_url(url: str) -> str | None:
    parts = urlparse(url).path.strip("/").split("/")
    if len(parts) == 4 and parts[:3] == ["images", "champions", "icons"]:
        return _emoji_key(parts[-1].removesuffix(".png"))
    if len(parts) == 5 and parts[:3] == ["images", "champions", "battlerites"]:
        return _emoji_key(parts[-1].removesuffix(".png"))
    return None


def _emoji_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


def champion_icon_url(champion: str | None) -> str | None:
    """Return BattleVive's champion icon URL for the forum-post thumbnail."""
    if not champion:
        return None
    asset_name = CHAMPION_ICON_ASSET_NAMES.get(champion, re.sub(r"\s+", "", champion))
    return f"{CHAMPION_ICON_BASE_URL}/{quote(asset_name, safe='')}.png"


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


class GuideThreadMissing(RuntimeError):
    """A tracked forum post disappeared after Discord retained it in cache."""


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
        self._emoji_lookup: dict[str, str] = {}
        self._emojis_loaded = False

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
            await self._refresh_application_emojis()
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
                    try:
                        message_ids = await self._replace(thread, guide, row["managed_message_ids"])
                    except GuideThreadMissing:
                        await self._publish(channel, guild, config, guide)
                    else:
                        await self.database.upsert_guide_thread(guild_id, guide.source_id, thread.id, guide.last_modified, message_ids)

    @staticmethod
    def _has_permissions(guild: discord.Guild, channel: discord.ForumChannel) -> bool:
        member = guild.me
        if member is None:
            return False
        permissions = channel.permissions_for(member)
        return all((permissions.view_channel, permissions.send_messages, permissions.read_message_history, permissions.manage_threads, permissions.mention_everyone))

    async def _publish(self, channel: discord.ForumChannel, guild: discord.Guild, config: dict[str, Any], guide: GuideMetadata) -> None:
        markdown = normalize_discord_markdown(await self.content.fetch_markdown(guide.source_id), emoji_lookup=self._emoji_lookup)
        role = guild.get_role(config["guide_notification_role_id"]) if config.get("guide_notification_role_id") else None
        prefix = role.mention + "\n" if role is not None else ""
        chunks = split_markdown(markdown, limit=DISCORD_MESSAGE_LIMIT - len(prefix))
        thread_with_message = await channel.create_thread(
            name=guide.title[:100],
            content=prefix + chunks[0],
            embed=self._guide_embed(guide, self._emoji_lookup),
            allowed_mentions=discord.AllowedMentions(roles=[role] if role else [], everyone=False, users=False),
        )
        thread = thread_with_message.thread
        message_ids = [thread_with_message.message.id]
        for chunk in chunks[1:]:
            message = await thread.send(chunk, allowed_mentions=discord.AllowedMentions.none())
            message_ids.append(message.id)
        await self.database.upsert_guide_thread(config["guild_id"], guide.source_id, thread.id, guide.last_modified, message_ids)

    async def _replace(self, thread: discord.Thread, guide: GuideMetadata, managed_message_ids: Iterable[int]) -> list[int]:
        message_ids = list(managed_message_ids)
        managed = [thread.get_partial_message(message_id) for message_id in message_ids]
        markdown = normalize_discord_markdown(await self.content.fetch_markdown(guide.source_id), emoji_lookup=self._emoji_lookup)
        chunks = split_markdown(markdown)
        updated_ids: list[int] = []
        try:
            if managed:
                await managed[0].edit(content=chunks[0], embed=self._guide_embed(guide, self._emoji_lookup), allowed_mentions=discord.AllowedMentions.none())
                updated_ids.append(managed[0].id)
                for message, chunk in zip(managed[1:], chunks[1:]):
                    await message.edit(content=chunk, allowed_mentions=discord.AllowedMentions.none())
                    updated_ids.append(message.id)
                for message in managed[len(chunks):]:
                    await message.delete()
                for chunk in chunks[len(managed):]:
                    message = await thread.send(chunk, allowed_mentions=discord.AllowedMentions.none())
                    updated_ids.append(message.id)
            else:
                first = await thread.send(chunks[0], embed=self._guide_embed(guide, self._emoji_lookup), allowed_mentions=discord.AllowedMentions.none())
                updated_ids.append(first.id)
                for chunk in chunks[1:]:
                    message = await thread.send(chunk, allowed_mentions=discord.AllowedMentions.none())
                    updated_ids.append(message.id)
            await thread.edit(name=guide.title[:100])
        except discord.NotFound as error:
            raise GuideThreadMissing from error
        return updated_ids

    @staticmethod
    def _guide_embed(guide: GuideMetadata, emoji_lookup: Mapping[str, str]) -> discord.Embed:
        champion_emoji = emoji_lookup.get(_emoji_key(guide.champion or ""))
        title = f"{champion_emoji} {guide.title}" if champion_emoji else guide.title
        embed = discord.Embed(title=title, url=guide.url)
        if icon_url := champion_icon_url(guide.champion):
            embed.set_thumbnail(url=icon_url)
        return embed

    async def _refresh_application_emojis(self) -> None:
        if self._emojis_loaded:
            return
        fetch = getattr(self.bot, "fetch_application_emojis", None)
        try:
            emojis = await fetch() if callable(fetch) else getattr(self.bot, "emojis", [])
        except Exception:
            logger.exception("Could not load application emojis for guide rendering.")
            return
        self._emoji_lookup = {
            _emoji_key(str(emoji.name)): str(emoji)
            for emoji in emojis
            if getattr(emoji, "name", None)
        }
        self._emojis_loaded = True

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

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
    """Normalize Markdown for Discord by removing unsupported horizontal rules and converting supported guide images to emoji references.
    
    Parameters:
        markdown (str): Markdown content to normalize.
        emoji_lookup (Mapping[str, str] | None): Optional mapping of normalized image identifiers to Discord emoji references.
    
    Returns:
        str: Discord-compatible Markdown with surrounding whitespace removed.
    """
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
    """Extract the normalized emoji identifier from a supported champion image URL.
    
    Parameters:
    	url (str): The image URL to inspect.
    
    Returns:
    	str | None: The normalized emoji identifier, or `None` if the URL is unsupported.
    """
    parts = urlparse(url).path.strip("/").split("/")
    if len(parts) == 4 and parts[:3] == ["images", "champions", "icons"]:
        return _emoji_key(parts[-1].removesuffix(".png"))
    if len(parts) == 5 and parts[:3] == ["images", "champions", "battlerites"]:
        return _emoji_key(parts[-1].removesuffix(".png"))
    return None


def _emoji_key(value: str) -> str:
    """Normalize a value into a lowercase identifier containing letters, digits, and underscores."""
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


def champion_icon_url(champion: str | None) -> str | None:
    """Return BattleVive's champion icon URL for the forum-post thumbnail."""
    if not champion:
        return None
    asset_name = CHAMPION_ICON_ASSET_NAMES.get(champion, re.sub(r"\s+", "", champion))
    return f"{CHAMPION_ICON_BASE_URL}/{quote(asset_name, safe='')}.png"


def split_markdown(markdown: str, *, limit: int = DISCORD_MESSAGE_LIMIT) -> list[str]:
    """
    Split Markdown into chunks that preserve all content within the specified size limit.
    
    Parameters:
    	markdown (str): Markdown content to split.
    	limit (int): Maximum number of characters per chunk.
    
    Returns:
    	list[str]: Markdown chunks, each no longer than the specified limit.
    
    Raises:
    	ValueError: If the limit is less than 2.
    """
    if limit < 2:
        raise ValueError("message limit must permit content")
    if not markdown:
        return [""]
    chunks: list[str] = []
    remaining = markdown
    while len(remaining) > limit:
        boundary = max(remaining.rfind("\n\n", 0, limit), remaining.rfind("\n", 0, limit))
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
        """
        Initialize the guide thread service with its dependencies and reconciliation interval.
        
        Parameters:
        	bot (Any): Discord bot instance used to manage forum threads.
        	database (Any): Persistence service for guide thread records.
        	catalog (GuideCatalogSource): Source of guide metadata.
        	content (GuideContentSource): Source of guide content.
        	reconcile_interval (float): Interval, in seconds, between periodic reconciliations.
        """
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
        """Start the background reconciliation tasks and request an initial reconciliation."""
        if self._tasks:
            return
        self._closed.clear()
        self._tasks = [
            asyncio.create_task(self._worker(), name="guide-thread-worker"),
            asyncio.create_task(self._periodic(), name="guide-thread-periodic"),
        ]
        self.request_reconciliation()

    async def stop(self) -> None:
        """Stop the service and wait for all background tasks to finish."""
        self._closed.set()
        self._requested.set()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        for source in (self.catalog, self.content):
            try:
                await source.close()
            except Exception:
                logger.exception("Could not close guide source during shutdown.")

    def is_running(self) -> bool:
        """Determine whether all service tasks are currently active.
        
        Returns:
        	bool: `true` if the service has active tasks and is not closed, `false` otherwise.
        """
        return bool(self._tasks) and not self._closed.is_set() and all(not task.done() for task in self._tasks)

    def request_reconciliation(self, *_args: object) -> None:
        """Request a guide reconciliation run."""
        self._requested.set()

    async def _worker(self) -> None:
        """Process reconciliation requests until the service is closed.
        
        Waits for Discord readiness before running each reconciliation pass and logs
        unexpected failures without stopping the worker.
        """
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
        """Request guide reconciliation at regular intervals until the service is closed."""
        while not self._closed.is_set():
            try:
                await asyncio.wait_for(self._closed.wait(), timeout=self.reconcile_interval)
            except TimeoutError:
                self.request_reconciliation()

    async def reconcile_all(self) -> None:
        """Reconcile all configured guilds with the current guide catalog."""
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
        """
        Synchronize the configured guild's guide forum threads with the current guide catalog.
        
        Parameters:
            config (dict[str, Any]): Guild and guide-forum configuration.
            guides (list[GuideMetadata]): Current guide metadata to publish or update.
        
        Raises:
            RuntimeError: If the configured guild is unavailable or the guide forum is
                missing or lacks the required permissions.
        """
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
        """
        Determine whether the bot has the permissions required to manage the forum channel.
        
        Parameters:
        	guild (discord.Guild): Guild containing the bot member.
        	channel (discord.ForumChannel): Forum channel to check.
        
        Returns:
        	bool: `true` if the bot has all required permissions, `false` otherwise.
        """
        member = guild.me
        if member is None:
            return False
        permissions = channel.permissions_for(member)
        return all((permissions.view_channel, permissions.send_messages, permissions.send_messages_in_threads, permissions.read_message_history, permissions.manage_threads, permissions.mention_everyone))

    async def _publish(self, channel: discord.ForumChannel, guild: discord.Guild, config: dict[str, Any], guide: GuideMetadata) -> None:
        """Publish a guide as a Discord forum thread and record its message metadata."""
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
        """
        Update a guide thread's managed messages and title to reflect the current guide content.
        
        Parameters:
            thread (discord.Thread): The forum thread to update.
            guide (GuideMetadata): Metadata for the guide being synchronized.
            managed_message_ids (Iterable[int]): IDs of messages currently managed by the service.
        
        Returns:
            list[int]: IDs of the messages retained or created during the update.
        
        Raises:
            GuideThreadMissing: If the thread or one of its managed messages no longer exists.
        """
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
                    try:
                        await message.delete()
                    except discord.NotFound:
                        pass
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
        """
        Create an embed for a guide with its title, link, and champion branding.
        
        Parameters:
            guide (GuideMetadata): Guide metadata used for the embed title and URL.
            emoji_lookup (Mapping[str, str]): Mapping of normalized champion names to Discord emoji mentions.
        
        Returns:
            discord.Embed: An embed linking to the guide and optionally displaying its champion emoji and thumbnail.
        """
        champion_emoji = emoji_lookup.get(_emoji_key(guide.champion or ""))
        title = f"{champion_emoji} {guide.title}" if champion_emoji else guide.title
        embed = discord.Embed(title=title, url=guide.url)
        if icon_url := champion_icon_url(guide.champion):
            embed.set_thumbnail(url=icon_url)
        return embed

    async def _refresh_application_emojis(self) -> None:
        """Load application emojis and build a normalized name lookup for guide rendering."""
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
        """
        Remove a Discord thread for a guide that no longer exists.
        
        Parameters:
            guild (discord.Guild): Guild containing the tracked thread.
            row (dict[str, Any]): Database record identifying the thread and source guide.
            delete (bool): Whether to delete the thread instead of archiving it.
        """
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

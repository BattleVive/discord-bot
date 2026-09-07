"""Guide-thread reconciliation using the original Discord guide presentation."""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
import re
from typing import Any
from urllib.parse import quote
from urllib.parse import urlparse

import discord

from .logs import logger


DISCORD_MESSAGE_LIMIT = 2_000
CHAMPION_ICON_BASE_URL = "https://battlevive.com/images/champions/icons"
CHAMPION_ICON_ASSET_NAMES = {"Shen Rao": "Shen-Rao"}
_MARKDOWN_IMAGE = re.compile(r"!\[[^\]]*\]\((<?[^)\s>]+>?)(?:\s+[^)]*)?\)")
_GUIDE_IMAGE_URL = re.compile(r"https://battlevive\.com/images/champions/(?:icons|battlerites)/[^\s<>()]+\.png")
_HORIZONTAL_RULE = re.compile(r"(?m)^[ \t]*(?:-{3,}|\*{3,}|_{3,})[ \t]*$")
_FENCED_CODE = re.compile(r"^[ \t]*(`{3,}|~{3,})")
_INLINE_CODE = re.compile(r"(`+[^`\n]*`+)")


def _emoji_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


def _champion_emoji_key(value: str) -> str:
    return _emoji_key(value).replace("_", "")


def _emoji_key_from_url(url: str) -> str | None:
    parts = urlparse(url).path.strip("/").split("/")
    if len(parts) == 4 and parts[:3] == ["images", "champions", "icons"]:
        return _champion_emoji_key(parts[-1].removesuffix(".png"))
    if len(parts) == 5 and parts[:3] == ["images", "champions", "battlerites"]:
        return _emoji_key(parts[-1].removesuffix(".png"))
    return None


def _emoji_for_url(url: str, emoji_lookup: Mapping[str, str]) -> str:
    key = _emoji_key_from_url(url)
    return emoji_lookup.get(key, f":{key}:") if key else url


def _rewrite_image_urls(segment: str, emoji_lookup: Mapping[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        following = segment[match.end()] if match.end() < len(segment) else ""
        separator = " " if following and not following.isspace() and following != "!" else ""
        return _emoji_for_url(match.group(1).strip("<>"), emoji_lookup) + separator

    return _GUIDE_IMAGE_URL.sub(
        lambda match: _emoji_for_url(match.group(), emoji_lookup),
        _MARKDOWN_IMAGE.sub(replace, segment),
    )


def normalize_discord_markdown(markdown: str, emoji_lookup: Mapping[str, str] | None = None) -> str:
    """Preserve v1 guide formatting while replacing supported image URLs with emojis."""
    emojis = emoji_lookup or {}
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
        normalized.append(line if in_fenced_code else "".join(
            segment if _INLINE_CODE.fullmatch(segment) else _rewrite_image_urls(segment, emojis)
            for segment in _INLINE_CODE.split(line)
        ))
    return "\n".join(normalized).strip()


def champion_icon_url(champion: str | None) -> str | None:
    if not champion:
        return None
    asset_name = CHAMPION_ICON_ASSET_NAMES.get(champion, re.sub(r"\s+", "", champion))
    return f"{CHAMPION_ICON_BASE_URL}/{quote(asset_name, safe='')}.png"


def guide_fingerprint(record: Mapping[str, object]) -> str | None:
    """Return the upstream change marker when the catalog provides one.

    Without a source marker a body-only edit cannot safely be detected from
    the catalog, so that guide continues to fetch its Markdown.
    """
    for field in ("updated_at", "updatedAt", "last_modified", "lastModified"):
        value = record.get(field)
        if isinstance(value, str) and value.strip():
            return f"guide-revision-{value.strip()}"
    return None


@dataclass(frozen=True, slots=True)
class Guide:
    number: int
    title: str
    markdown: str | None
    champion: str | None = None
    url: str | None = None
    fingerprint: str | None = None


@dataclass(frozen=True, slots=True)
class GuidePublication:
    thread_id: int
    message_ids: tuple[int, ...]


class GuidePublicationMetadataMissing(RuntimeError):
    """A legacy tracked thread has no safe set of bot-managed messages."""


class GuideReconciler:
    def __init__(self, publications: Any, discord: Any) -> None:
        self._publications = publications
        self._discord = discord

    async def reconcile(self, guild_id: int, guides: list[Guide]) -> None:
        existing = {str(row["publication_key"]): row for row in await self._publications.list_for_feature(guild_id, "guide")}
        desired = {f"guide:{guide.number}": guide for guide in guides}
        for key, guide in desired.items():
            previous = existing.pop(key, None)
            if guide.fingerprint is not None and previous is not None and previous.get("fingerprint") == guide.fingerprint:
                continue
            if guide.markdown is None:
                raise RuntimeError("changed guide is missing Markdown")
            publication = await self._discord.create_or_update(guide, previous)
            await self._publications.upsert(
                guild_id, "guide", key, publication.thread_id, None, publication.thread_id, guide.fingerprint,
                {"title": guide.title, "message_ids": list(publication.message_ids)},
            )
        for key, publication in existing.items():
            thread_id = publication.get("thread_id")
            if isinstance(thread_id, int):
                await self._discord.archive(thread_id)
            await self._publications.delete(guild_id, "guide", key)


class DiscordGuidePublisher:
    """Discord-only side of guide publication; database changes happen after Discord succeeds."""

    def __init__(self, bot: discord.Client, guild_id: int, forum_channel_id: int,
                 *, delete_on_removal: bool = False, emoji_lookup: Mapping[str, str] | None = None) -> None:
        self._bot = bot
        self._guild_id = guild_id
        self._forum_channel_id = forum_channel_id
        self._delete_on_removal = delete_on_removal
        self._emoji_lookup = emoji_lookup or {}

    async def create_or_update(self, guide: Guide, prior: dict[str, object] | None) -> GuidePublication:
        guild = self._bot.get_guild(self._guild_id)
        if guild is None:
            raise RuntimeError("configured guild is unavailable")
        forum = guild.get_channel(self._forum_channel_id)
        if not isinstance(forum, discord.ForumChannel):
            raise RuntimeError("configured guide forum is unavailable")
        if guide.markdown is None:
            raise RuntimeError("changed guide is missing Markdown")
        content = _guide_chunks(normalize_discord_markdown(guide.markdown, self._emoji_lookup))
        embed = self._guide_embed(guide)
        prior_thread_id = prior.get("thread_id") if prior is not None else None
        if isinstance(prior_thread_id, int):
            thread = guild.get_thread(prior_thread_id)
            if thread is None:
                try:
                    fetched = await self._bot.fetch_channel(prior_thread_id)
                    thread = fetched if isinstance(fetched, discord.Thread) else None
                except discord.NotFound:
                    thread = None
            if thread is not None and thread.parent_id == forum.id:
                await thread.edit(name=guide.title[:100], archived=False)
                stored = prior.get("metadata", {}) if prior is not None else {}
                message_ids = stored.get("message_ids", []) if isinstance(stored, dict) else []
                try:
                    return await self._replace(thread, content, embed, message_ids)
                except GuidePublicationMetadataMissing:
                    await thread.edit(archived=True, reason="Guide publication metadata is missing")
            if thread is not None:
                await retire_relocated_thread(thread, forum.id, delete_on_removal=self._delete_on_removal)
        created = await forum.create_thread(
            name=guide.title[:100], content=content[0], embed=embed,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        message_ids = [created.message.id]
        for chunk in content[1:]:
            message = await created.thread.send(chunk, allowed_mentions=discord.AllowedMentions.none())
            message_ids.append(message.id)
        return GuidePublication(created.thread.id, tuple(message_ids))

    async def _replace(self, thread: discord.Thread, chunks: list[str], embed: discord.Embed,
                       stored_ids: object) -> GuidePublication:
        ids = [message_id for message_id in stored_ids if isinstance(message_id, int)] if isinstance(stored_ids, list) else []
        if not ids:
            raise GuidePublicationMetadataMissing("guide publication metadata is missing")
        managed = [thread.get_partial_message(message_id) for message_id in ids]
        updated: list[int] = []
        try:
            if managed:
                await managed[0].edit(content=chunks[0], embed=embed, allowed_mentions=discord.AllowedMentions.none())
                updated.append(managed[0].id)
                for message, chunk in zip(managed[1:], chunks[1:]):
                    await message.edit(content=chunk, allowed_mentions=discord.AllowedMentions.none())
                    updated.append(message.id)
                for message in managed[len(chunks):]:
                    try:
                        await message.delete()
                    except discord.NotFound:
                        pass
                for chunk in chunks[len(managed):]:
                    message = await thread.send(chunk, allowed_mentions=discord.AllowedMentions.none())
                    updated.append(message.id)
            else:
                message = await thread.send(chunks[0], embed=embed, allowed_mentions=discord.AllowedMentions.none())
                updated.append(message.id)
                for chunk in chunks[1:]:
                    message = await thread.send(chunk, allowed_mentions=discord.AllowedMentions.none())
                    updated.append(message.id)
        except discord.NotFound as error:
            raise RuntimeError("tracked guide message disappeared") from error
        return GuidePublication(thread.id, tuple(updated))

    def _guide_embed(self, guide: Guide) -> discord.Embed:
        champion_emoji = self._emoji_lookup.get(_champion_emoji_key(guide.champion or ""))
        title = f"{champion_emoji} {guide.title}" if champion_emoji else guide.title
        embed = discord.Embed(title=title, url=guide.url)
        if icon_url := champion_icon_url(guide.champion):
            embed.set_thumbnail(url=icon_url)
        return embed

    async def archive(self, thread_id: int) -> None:
        try:
            channel = await self._bot.fetch_channel(thread_id)
        except discord.NotFound:
            return
        if not isinstance(channel, discord.Thread):
            return
        if self._delete_on_removal:
            await channel.delete(reason="Guide removed upstream")
        else:
            await channel.edit(archived=True, reason="Guide removed upstream")


async def retire_relocated_thread(thread: Any, forum_id: int, *, delete_on_removal: bool) -> bool:
    """Retire a tracked guide thread before replacing it in another forum."""
    if getattr(thread, "parent_id", None) == forum_id:
        return False
    if delete_on_removal:
        await thread.delete(reason="Guide forum channel changed")
    else:
        await thread.edit(archived=True, reason="Guide forum channel changed")
    return True


def _guide_chunks(markdown: str, *, limit: int = 2_000) -> list[str]:
    if not markdown:
        return [""]
    chunks: list[str] = []
    remaining = markdown
    while len(remaining) > limit:
        split_at = max(remaining.rfind("\n\n", 0, limit), remaining.rfind("\n", 0, limit), 0)
        chunks.append(remaining[:split_at] if split_at else remaining[:limit])
        remaining = remaining[split_at:] if split_at else remaining[limit:]
    return chunks + [remaining]


async def application_emoji_lookup(bot: discord.Client) -> dict[str, str]:
    """Return the v1 normalized application-emoji map without making it required."""
    fetch = getattr(bot, "fetch_application_emojis", None)
    try:
        emojis = await fetch() if callable(fetch) else getattr(bot, "emojis", [])
    except Exception:
        return {}
    return {
        _emoji_key(str(emoji.name)): str(emoji)
        for emoji in emojis
        if getattr(emoji, "name", None)
    }


async def sync_configured_guides(bot: discord.Client, pool: Any, upstream: Any, guild_id: int) -> bool:
    """Synchronize one configured guild; False means guide publication is not configured."""
    from .repositories import GuildConfigRepository
    from .repositories import PublicationRepository

    async with pool.acquire() as connection:
        config = await GuildConfigRepository(connection).get(guild_id)
        if config is None or not isinstance(config.get("guide_forum_channel_id"), int):
            return False
        catalog = await upstream.get_result("/guides", require_fresh=True)
        records = catalog.data.get("guides")
        if not isinstance(records, list):
            raise RuntimeError("upstream guide catalog was invalid")
        publications = PublicationRepository(connection)
        existing = {
            str(publication["publication_key"]): publication
            for publication in await publications.list_for_feature(guild_id, "guide")
        }
        guides: list[Guide] = []
        for record in records:
            if not isinstance(record, dict):
                raise RuntimeError("upstream guide catalog was invalid")
            number = record.get("number", record.get("guide_number", record.get("id")))
            title = record.get("title")
            if isinstance(number, bool) or not isinstance(number, int) or number <= 0 or not isinstance(title, str) or not title.strip():
                raise RuntimeError("upstream guide catalog was invalid")
            champion = record.get("champion")
            url = record.get("url")
            fingerprint = guide_fingerprint(record)
            previous = existing.get(f"guide:{number}")
            changed = fingerprint is None or previous is None or previous.get("fingerprint") != fingerprint
            markdown: str | None = None
            if changed:
                markdown = record.get("markdown")
                if not isinstance(markdown, str):
                    markdown = (await upstream.get_result(f"/guides/{number}/markdown", require_fresh=True)).data.get("markdown")
                if not isinstance(markdown, str):
                    raise RuntimeError("upstream guide Markdown was invalid")
            guides.append(Guide(
                number, title.strip(), markdown,
                champion=champion.strip() if isinstance(champion, str) and champion.strip() else None,
                url=url.strip() if isinstance(url, str) and url.strip() else None,
                fingerprint=fingerprint,
            ))
        publisher = DiscordGuidePublisher(bot, guild_id, config["guide_forum_channel_id"],
                                          delete_on_removal=bool(config.get("guide_auto_delete_on_removal")),
                                          emoji_lookup=await application_emoji_lookup(bot))
        await GuideReconciler(publications, publisher).reconcile(guild_id, guides)
    return True


class GuideService:
    """Periodically reconcile every configured guide forum without persistent jobs."""

    def __init__(self, bot: discord.Client, pool: Any, upstream: Any, *, interval: float = 300.0) -> None:
        self._bot, self._pool, self._upstream, self._interval = bot, pool, upstream, interval
        self._requested = asyncio.Event()
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="guide-publisher")
            self.request_reconciliation()

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    def request_reconciliation(self) -> None:
        self._requested.set()

    async def reconcile_guild(self, guild_id: int) -> bool:
        async with self._lock:
            return await sync_configured_guides(self._bot, self._pool, self._upstream, guild_id)

    async def reconcile_all(self) -> None:
        from .repositories import GuildConfigRepository

        async with self._lock:
            async with self._pool.acquire() as connection:
                configs = await GuildConfigRepository(connection).configured_guides()
            for config in configs:
                guild_id = config.get("guild_id")
                if not isinstance(guild_id, int):
                    continue
                try:
                    await sync_configured_guides(self._bot, self._pool, self._upstream, guild_id)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Guide reconciliation failed for guild %s", guild_id)

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
                logger.exception("Guide reconciliation pass failed")

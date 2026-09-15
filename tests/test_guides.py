"""Tests for Discord guide formatting, publication, and reconciliation."""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import call
import discord
import pytest

from battlevive_gateway.guides import Guide
from battlevive_gateway.guides import GuideReconciler
from battlevive_gateway.guides import GuidePublication
from battlevive_gateway.guides import DiscordGuidePublisher
from battlevive_gateway.guides import GuideService
from battlevive_gateway.guides import sync_configured_guides
from battlevive_gateway.guides import retire_relocated_thread
from battlevive_gateway.guides import champion_icon_url
from battlevive_gateway.guides import normalize_discord_markdown


class Publications:
    """Provide a publications test double."""
    def __init__(self) -> None:
        """Initialize the publications instance."""
        self.saved: list[tuple[object, ...]] = []
        self.deleted: list[tuple[int, str, str]] = []

    async def list_for_feature(self, _: int, __: str) -> list[dict[str, object]]:
        """Provide list for feature behavior for the test scenario."""
        return [{"publication_key": "guide:2", "thread_id": 22}]

    async def upsert(self, *args: object) -> None:
        """Provide upsert behavior for the test scenario."""
        self.saved.append(args)

    async def delete(self, guild_id: int, feature: str, key: str) -> None:
        """Provide delete behavior for the test scenario."""
        self.deleted.append((guild_id, feature, key))


class Discord:
    """Provide a Discord test double."""
    def __init__(self) -> None:
        """Initialize the Discord instance."""
        self.created: list[Guide] = []
        self.archived: list[int] = []

    async def create_or_update(self, guide: Guide, existing: object) -> GuidePublication:
        """Provide create or update behavior for the test scenario."""
        self.created.append(guide)
        return GuidePublication(existing.get("thread_id", 11) if isinstance(existing, dict) else 11, (11, 12))

    async def archive(self, thread_id: int) -> None:
        """Provide archive behavior for the test scenario."""
        self.archived.append(thread_id)


@pytest.mark.asyncio
async def test_guide_reconciliation_creates_and_archives_removed_guides() -> None:
    """Verify that guide reconciliation creates and archives removed guides."""
    publications, discord = Publications(), Discord()
    await GuideReconciler(publications, discord).reconcile(7, [Guide(1, "One", "# One")])
    assert discord.created == [Guide(1, "One", "# One")]
    assert discord.archived == [22]
    assert publications.deleted == [(7, "guide", "guide:2")]
    assert publications.saved[0][-1]["message_ids"] == [11, 12]


@pytest.mark.asyncio
async def test_relocated_guide_thread_is_archived_before_its_replacement_is_created() -> None:
    """Verify that relocated guide thread is archived before its replacement is created."""
    thread = type("Thread", (), {"parent_id": 10, "edit": pytest.importorskip("unittest.mock").AsyncMock()})()

    moved = await retire_relocated_thread(thread, forum_id=20, delete_on_removal=False)

    assert moved is True
    thread.edit.assert_awaited_once_with(  # type: ignore[attr-defined]
        archived=True, reason="Guide forum channel changed"
    )


@pytest.mark.asyncio
async def test_guide_service_reconciles_every_configured_guild(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that guide service reconciles every configured guild."""
    class Connection:
        """Provide a connection test double."""
        async def fetch(self, query: str) -> list[dict[str, int]]:
            """Provide fetch behavior for the test scenario."""
            assert "guide_forum_channel_id" in query
            return [{"guild_id": 7}, {"guild_id": 9}]

    class Acquire:
        """Provide a acquire test double."""
        async def __aenter__(self) -> Connection:
            """Enter the asynchronous context manager."""
            return Connection()
        async def __aexit__(self, *_: object) -> None:
            """Exit the asynchronous context manager."""
            return None

    pool = SimpleNamespace(acquire=lambda: Acquire())
    sync = AsyncMock(return_value=True)
    monkeypatch.setattr("battlevive_gateway.guides.sync_configured_guides", sync)

    await GuideService(object(), pool, object()).reconcile_all()

    assert [call.args[3] for call in sync.await_args_list] == [7, 9]


@pytest.mark.asyncio
async def test_guide_sync_uses_catalog_revision_without_refetching_unchanged_markdown() -> None:
    """Verify that guide sync uses catalog revision without refetching unchanged markdown."""
    class Connection:
        """Provide a connection test double."""
        async def fetchrow(self, query: str, guild_id: int) -> dict[str, object]:
            """Provide fetchrow behavior for the test scenario."""
            assert "guild_config" in query
            assert guild_id == 7
            return {"guide_forum_channel_id": 10}

        async def fetch(self, query: str, *_: object) -> list[dict[str, object]]:
            """Provide fetch behavior for the test scenario."""
            assert "discord_publications" in query
            return [{
                "publication_key": "guide:1",
                "channel_id": 11,
                "message_id": None,
                "thread_id": 12,
                "fingerprint": "guide-revision-2026-09-07T20:00:00Z",
                "metadata": {"message_ids": [13]},
            }]

    class Acquire:
        """Provide a acquire test double."""
        async def __aenter__(self) -> Connection:
            """Enter the asynchronous context manager."""
            return Connection()
        async def __aexit__(self, *_: object) -> None:
            """Exit the asynchronous context manager."""
            return None

    class Upstream:
        """Provide a upstream test double."""
        def __init__(self) -> None:
            """Initialize the upstream instance."""
            self.paths: list[str] = []

        async def get_result(self, path: str, *, require_fresh: bool) -> SimpleNamespace:
            """Provide get result behavior for the test scenario."""
            assert require_fresh is True
            self.paths.append(path)
            if path == "/guides":
                return SimpleNamespace(data={"guides": [{
                    "number": 1, "title": "One", "updated_at": "2026-09-07T20:00:00Z",
                }]})
            raise AssertionError(f"unexpected Markdown request: {path}")

    upstream = Upstream()

    assert await sync_configured_guides(SimpleNamespace(emojis=[]), SimpleNamespace(acquire=lambda: Acquire()), upstream, 7)
    assert upstream.paths == ["/guides"]


@pytest.mark.asyncio
async def test_missing_guide_message_metadata_republishes_in_the_existing_thread() -> None:
    """Verify that missing guide message metadata republishes in the existing thread."""
    old_thread = MagicMock(spec=discord.Thread)
    old_thread.id, old_thread.parent_id, old_thread.edit = 22, 10, AsyncMock()
    old_thread.send = AsyncMock(return_value=SimpleNamespace(id=34))
    forum = MagicMock(spec=discord.ForumChannel)
    forum.id = 10
    guild = SimpleNamespace(get_channel=lambda _: forum, get_thread=lambda _: old_thread)
    bot = SimpleNamespace(get_guild=lambda _: guild)
    publisher = DiscordGuidePublisher(bot, 7, 10)

    publication = await publisher.create_or_update(
        Guide(1, "One", "# One"), {"thread_id": 22, "metadata": {}}
    )

    assert publication == GuidePublication(22, (34,))
    old_thread.edit.assert_awaited_once_with(name="One", archived=False)
    old_thread.send.assert_awaited_once()
    forum.create_thread.assert_not_called()


def test_guide_markdown_keeps_code_and_replaces_battlerite_images_with_emojis() -> None:
    """Verify that guide markdown keeps code and replaces battlerite images with emojis."""
    markdown = (
        "![](https://battlevive.com/images/champions/battlerites/Varesh/Varesh_Mouse1_Control.png)**Silence**\n"
        "```\n![](https://battlevive.com/images/champions/battlerites/Varesh/Varesh_E_Offense.png)\n```\n---\n"
    )

    assert normalize_discord_markdown(
        markdown, {"varesh_mouse1_control": "<:varesh_mouse1_control:42>"}
    ) == (
        "<:varesh_mouse1_control:42> **Silence**\n"
        "```\n![](https://battlevive.com/images/champions/battlerites/Varesh/Varesh_E_Offense.png)\n```"
    )


def test_guide_champion_thumbnail_preserves_special_champion_spelling() -> None:
    """Verify that guide champion thumbnail preserves special champion spelling."""
    assert champion_icon_url("Shen Rao") == "https://battlevive.com/images/champions/icons/Shen-Rao.png"


def test_fresh_guide_installation_requires_tracked_publications() -> None:
    """Verify that fresh guide installation requires tracked publications."""
    source = inspect.getsource(__import__("battlevive_gateway.guides", fromlist=["DiscordGuidePublisher"]).DiscordGuidePublisher._replace)
    assert "history" not in source
    assert "thread.send" in source

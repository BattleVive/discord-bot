from __future__ import annotations

from datetime import UTC
from datetime import datetime
from types import SimpleNamespace

import discord
import pytest

from battlevive_bot.battlevive.guides import GuideMetadata
from battlevive_bot.guides import champion_icon_url
from battlevive_bot.guides import GuideThreadService
from battlevive_bot.guides import GuideThreadMissing
from battlevive_bot.guides import normalize_discord_markdown
from battlevive_bot.guides import split_markdown


def test_guide_thread_service_requires_sending_messages_in_threads() -> None:
    permissions = SimpleNamespace(
        view_channel=True,
        send_messages=True,
        send_messages_in_threads=False,
        read_message_history=True,
        manage_threads=True,
        mention_everyone=True,
    )
    channel = SimpleNamespace(permissions_for=lambda _member: permissions)
    guild = SimpleNamespace(me=object())

    assert GuideThreadService._has_permissions(guild, channel) is False


def test_normalize_discord_markdown_uses_missing_emoji_tokens_and_removes_rules() -> None:
    """Missing custom emojis stay readable without leaving BattleVive image URLs."""
    markdown = (
        "# Blossom control\n\n"
        "![](https://battlevive.com/images/champions/battlerites/Blossom/Blossom_E_Control.png)\n\n"
        "---\n\n"
        "**Keep bold**, [keep links](https://battlevive.com), and `keep code`."
    )

    assert normalize_discord_markdown(markdown) == (
        "# Blossom control\n\n"
        ":blossom_e_control:\n\n"
        "**Keep bold**, [keep links](https://battlevive.com), and `keep code`."
    )


def test_normalize_discord_markdown_separates_emoji_tokens_from_following_text() -> None:
    """A converted image token must not absorb the next heading or label."""
    markdown = "![](https://battlevive.com/images/champions/battlerites/Varesh/Varesh_Mouse1_Control.png)**Silence combo:**"

    assert normalize_discord_markdown(markdown) == (
        ":varesh_mouse1_control: "
        "**Silence combo:**"
    )


def test_normalize_discord_markdown_keeps_consecutive_battlerites_inline() -> None:
    markdown = (
        "![](https://battlevive.com/images/champions/battlerites/Varesh/Varesh_Mouse1_Control.png)"
        "![](https://battlevive.com/images/champions/battlerites/Varesh/Varesh_E_Offense.png)"
    )

    assert normalize_discord_markdown(markdown) == ":varesh_mouse1_control::varesh_e_offense:"


def test_normalize_discord_markdown_replaces_known_guide_images_with_custom_emojis() -> None:
    """Known champion and battlerite assets never leave image links in Discord."""
    markdown = (
        "![](https://battlevive.com/images/champions/icons/Varesh.png)\n"
        "![](https://battlevive.com/images/champions/battlerites/Varesh/Varesh_Mouse1_Control.png)"
    )

    assert normalize_discord_markdown(
        markdown,
        emoji_lookup={
            "varesh": "<:varesh:101>",
            "varesh_mouse1_control": "<:varesh_mouse1_control:102>",
        },
    ) == "<:varesh:101>\n<:varesh_mouse1_control:102>"


def test_normalize_discord_markdown_preserves_blank_lines_in_code_blocks() -> None:
    """Discord-supported code blocks must not be altered while removing rules."""
    markdown = "```\nfirst\n\n\nlast\n```\n\n---\n\nAfter"

    assert normalize_discord_markdown(markdown) == "```\nfirst\n\n\nlast\n```\n\nAfter"


def test_normalize_discord_markdown_preserves_image_syntax_in_code() -> None:
    """Literal image syntax in a code block is not an image that Discord should preview."""
    markdown = "Use `![](https://example.com/inline.png)`\n```\n![](https://example.com/block.png)\n```"

    assert normalize_discord_markdown(markdown) == markdown


def test_split_markdown_never_includes_a_newline_at_the_limit() -> None:
    """A newline at the limit must not make a Discord message 2,001 characters long."""
    markdown = "a" * 2_000 + "\nnext"

    assert split_markdown(markdown) == ["a" * 2_000, "\nnext"]


def test_guide_embed_uses_the_champion_emoji_in_its_linked_title() -> None:
    guide = GuideMetadata(
        source_id="3",
        title="Ruh Kaan guide",
        url="https://battlevive.com/battlerite-guides/3",
        last_modified=datetime(2026, 9, 4, tzinfo=UTC),
        champion="Ruh Kaan",
    )

    embed = GuideThreadService._guide_embed(guide, {"ruh_kaan": "<:ruh_kaan:101>"})

    assert embed.title == "<:ruh_kaan:101> Ruh Kaan guide"
    assert embed.url == guide.url
    assert embed.thumbnail.url == champion_icon_url("Ruh Kaan")


class FakeMessage:
    def __init__(self, message_id: int, content: str) -> None:
        """Initialize a fake message with an identifier and visible content.
        
        Parameters:
            message_id (int): The message identifier.
            content (str): The message content.
        """
        self.id = message_id
        self.content = content
        self.deleted = False
        self.embed = None

    async def edit(self, *, content: str, embed: object = None, **_kwargs: object) -> None:
        """Update the simulated message content and embed."""
        self.content = content
        self.embed = embed

    async def delete(self) -> None:
        """Mark the simulated message as deleted."""
        self.deleted = True


class FakeThread:
    def __init__(self) -> None:
        """Initialize the fake thread with existing messages and an empty sent-message list."""
        self.messages = {1: FakeMessage(1, "old first"), 2: FakeMessage(2, "old second")}
        self.sent: list[FakeMessage] = []

    def get_partial_message(self, message_id: int) -> FakeMessage:
        """Retrieve a tracked message by its identifier.
        
        Parameters:
        	message_id (int): The identifier of the message to retrieve.
        
        Returns:
        	FakeMessage: The tracked message associated with the identifier.
        """
        return self.messages[message_id]

    async def send(self, content: str, **_kwargs: object) -> FakeMessage:
        """Send content to the fake thread and record the resulting message.
        
        Parameters:
            content (str): Message content to store.
        
        Returns:
            FakeMessage: The newly created message.
        """
        message = FakeMessage(100 + len(self.sent), content)
        self.sent.append(message)
        return message

    async def edit(self, **_kwargs: object) -> None:
        """Simulate editing the message without changing its state."""
        pass


class FakeContentSource:
    async def fetch_markdown(self, _source_id: str) -> str:
        """
        Provide the guide's markdown content.
        
        Parameters:
            _source_id (str): Identifier of the guide content source.
        
        Returns:
            str: Markdown containing a Ruh Kaan champion image and updated text.
        """
        return "![](https://battlevive.com/images/champions/icons/Ruh-Kaan.png)\n\nUpdated"


class MissingMessage(FakeMessage):
    async def edit(self, **_kwargs: object) -> None:
        """Raise a Discord not-found error when an edit is attempted."""
        raise discord.NotFound(
            SimpleNamespace(status=404, reason="Not Found"),
            {"code": 10003, "message": "Unknown Channel"},
        )


class MissingDeleteMessage(FakeMessage):
    async def delete(self) -> None:
        """Simulate a continuation deleted between reconciliation and deletion."""
        raise discord.NotFound(
            SimpleNamespace(status=404, reason="Not Found"),
            {"code": 10008, "message": "Unknown Message"},
        )


@pytest.mark.asyncio
async def test_replace_reports_a_staff_deleted_forum_post_for_republication() -> None:
    """A stale cached thread must be recreated instead of trapping reconciliation."""
    thread = FakeThread()
    thread.messages[1] = MissingMessage(1, "old first")
    guide = GuideMetadata(
        source_id="3",
        title="Ruh Kaan guide",
        url="https://battlevive.com/battlerite-guides/3",
        last_modified=datetime(2026, 9, 4, tzinfo=UTC),
        champion="Ruh Kaan",
    )
    service = GuideThreadService(None, None, None, FakeContentSource())

    with pytest.raises(GuideThreadMissing):
        await service._replace(thread, guide, [1])


@pytest.mark.asyncio
async def test_replace_uses_tracked_messages_without_visible_markers() -> None:
    """Only tracked guide messages change; their content contains no service metadata."""
    thread = FakeThread()
    guide = GuideMetadata(
        source_id="3",
        title="Ruh Kaan guide",
        url="https://battlevive.com/battlerite-guides/3",
        last_modified=datetime(2026, 9, 4, tzinfo=UTC),
        champion="Ruh Kaan",
    )
    service = GuideThreadService(None, None, None, FakeContentSource())
    service._emoji_lookup = {"ruh_kaan": "<:ruh_kaan:101>"}

    message_ids = await service._replace(thread, guide, [1, 2])

    assert message_ids == [1]
    assert thread.messages[1].content == "<:ruh_kaan:101>\n\nUpdated"
    assert thread.messages[1].embed.title == "<:ruh_kaan:101> Ruh Kaan guide"
    assert thread.messages[1].embed.thumbnail.url == champion_icon_url("Ruh Kaan")
    assert thread.messages[2].deleted is True


@pytest.mark.asyncio
async def test_replace_keeps_the_forum_post_when_a_surplus_message_is_already_deleted() -> None:
    """A missing continuation is not evidence that the whole forum post disappeared."""
    thread = FakeThread()
    thread.messages[2] = MissingDeleteMessage(2, "old second")
    guide = GuideMetadata(
        source_id="3",
        title="Ruh Kaan guide",
        url="https://battlevive.com/battlerite-guides/3",
        last_modified=datetime(2026, 9, 4, tzinfo=UTC),
        champion="Ruh Kaan",
    )
    service = GuideThreadService(None, None, None, FakeContentSource())

    assert await service._replace(thread, guide, [1, 2]) == [1]


class CloseableSource:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_stop_closes_guide_sources_after_stopping_workers() -> None:
    """Guide-owned HTTP resources must not outlive the Discord service."""
    catalog = CloseableSource()
    content = CloseableSource()
    service = GuideThreadService(None, None, catalog, content)

    await service.stop()

    assert catalog.closed is True
    assert content.closed is True

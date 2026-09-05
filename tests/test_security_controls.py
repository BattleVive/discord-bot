from __future__ import annotations

import asyncio
from io import BytesIO
import logging
import os
from pathlib import Path
from types import SimpleNamespace

import aiohttp
import pytest

discord = pytest.importorskip("discord")
pytest.importorskip("asyncpg")

from battlevive_bot import bot as bot_module
from battlevive_bot import logs
from battlevive_bot import settings
from battlevive_bot.battlevive.supabase import SupabaseTransport
from battlevive_bot.settings import SettingsError
from battlevive_bot.settings import parse_command_guild_id


class FakeResponse:
    def __init__(self) -> None:
        """Initialize a fake interaction response for testing."""
        self.messages: list[dict[str, object]] = []
        self.deferred = False

    async def send_message(self, content: str, **kwargs: object) -> None:
        """Record a response message."""
        self.messages.append({"content": content, **kwargs})

    async def defer(self, *, ephemeral: bool) -> None:
        """Defer the interaction response."""
        assert ephemeral is True
        self.deferred = True

    def is_done(self) -> bool:
        """Check whether the response has been sent or deferred."""
        return self.deferred or bool(self.messages)


class FakeFollowup:
    def __init__(self) -> None:
        """Initialize a fake followup for testing."""
        self.messages: list[dict[str, object]] = []

    async def send(self, content: str | None = None, **kwargs: object) -> None:
        """Record a followup message with optional file attachments."""
        record: dict[str, object] = {"content": content, **kwargs}
        files = kwargs.get("files")
        if files:
            captured: list[tuple[str, bytes]] = []
            for attachment in files:
                attachment.fp.seek(0)
                captured.append((attachment.filename, attachment.fp.read()))
            record["captured_files"] = captured
        self.messages.append(record)


def interaction(
    *,
    guild: bool = True,
    manage_guild: bool = True,
    manage_roles: bool = True,
) -> SimpleNamespace:
    """Create a fake interaction with configurable guild and permission settings."""
    guild_object = (
        SimpleNamespace(
            id=789,
            name="Test Guild",
            me=SimpleNamespace(
                guild_permissions=SimpleNamespace(manage_roles=True),
            ),
        )
        if guild
        else None
    )
    return SimpleNamespace(
        guild=guild_object,
        user=SimpleNamespace(
            id=123,
            guild_permissions=SimpleNamespace(
                manage_guild=manage_guild,
                manage_roles=manage_roles,
            ),
        ),
        response=FakeResponse(),
        followup=FakeFollowup(),
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("", None),
        ("  ", None),
        ("123456789012345678", 123456789012345678),
    ],
)
def test_command_guild_id_parser(value: str | None, expected: int | None) -> None:
    """Verify that command guild ID parser correctly handles valid snowflakes and empty values."""
    assert parse_command_guild_id(value) == expected


@pytest.mark.parametrize("value", ["0", "-1", "+1", "abc", str(2**64)])
def test_command_guild_id_parser_rejects_invalid_values(value: str) -> None:
    """Verify that command guild ID parser rejects non-numeric and invalid snowflake values."""
    with pytest.raises(SettingsError, match="positive Discord snowflake"):
        parse_command_guild_id(value)


def test_runtime_settings_validation_is_secret_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that settings validation errors do not expose secrets in error messages."""
    monkeypatch.setattr(settings, "DATABASE_URL", "")
    monkeypatch.setattr(settings, "DISCORD_BOT_TOKEN", "private-token")
    monkeypatch.setattr(settings, "SUPABASE_API_KEY", "private-api-key")
    monkeypatch.setattr(settings, "SUPABASE_URL", "https://supabase.test")

    with pytest.raises(SettingsError) as caught:
        settings.validate_runtime_settings()

    assert "DATABASE_URL" in str(caught.value)
    assert "private-token" not in str(caught.value)
    assert "private-api-key" not in str(caught.value)


@pytest.mark.asyncio
async def test_setup_syncs_globally_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that setup_hook syncs commands globally when no development guild is configured."""
    calls: list[dict[str, object]] = []

    async def init_pool(dsn: str) -> None:
        """Stub database pool initialization."""
        return None

    class LeaderboardService:
        def __init__(self, *args: object) -> None:
            """Initialize stub service."""
            return None

        def start(self) -> None:
            """Stub service start."""
            return None

    class ActiveLobbyService(LeaderboardService):
        """Stub active lobby service."""
        pass

    async def sync(**kwargs: object) -> list[object]:
        """Record command sync calls."""
        calls.append(kwargs)
        return []

    monkeypatch.setattr(bot_module, "init_pool", init_pool)
    monkeypatch.setattr(bot_module, "LeaderboardService", LeaderboardService)
    monkeypatch.setattr(bot_module, "ActiveLobbyService", ActiveLobbyService)
    monkeypatch.setattr(bot_module, "GuideThreadService", LeaderboardService)
    monkeypatch.setattr(bot_module, "DISCORD_COMMAND_GUILD_ID", None)
    monkeypatch.setattr(bot_module.bot.tree, "sync", sync)
    monkeypatch.setattr(
        bot_module.bot.tree,
        "copy_global_to",
        lambda **kwargs: pytest.fail("global sync must not copy to a guild"),
    )
    monkeypatch.setattr(bot_module.revalidate_tokens, "start", lambda: None)
    monkeypatch.setattr(
        bot_module.refresh_infrequently_changing_data,
        "start",
        lambda: None,
    )
    monkeypatch.setattr(
        bot_module.refresh_frequently_changing_data,
        "start",
        lambda: None,
    )
    monkeypatch.setattr(bot_module.publish_health, "start", lambda: None)
    monkeypatch.setattr(bot_module.bot, "leaderboard_service", None)
    monkeypatch.setattr(bot_module.bot, "active_lobby_service", None)
    monkeypatch.setattr(bot_module.bot, "guide_thread_service", None)

    await bot_module.setup_hook()

    assert calls == [{}]


@pytest.mark.asyncio
async def test_setup_can_sync_to_a_development_guild(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that setup_hook syncs commands to a specific guild when configured."""
    copied: list[int] = []
    synced: list[int] = []

    async def init_pool(dsn: str) -> None:
        """Stub database pool initialization."""
        return None

    class LeaderboardService:
        def __init__(self, *args: object) -> None:
            """Initialize stub service."""
            return None

        def start(self) -> None:
            """Stub service start."""
            return None

    class ActiveLobbyService(LeaderboardService):
        """Stub active lobby service."""
        pass

    async def sync(*, guild: object) -> list[object]:
        """Record guild-specific command sync."""
        synced.append(guild.id)
        return []

    monkeypatch.setattr(bot_module, "init_pool", init_pool)
    monkeypatch.setattr(bot_module, "LeaderboardService", LeaderboardService)
    monkeypatch.setattr(bot_module, "ActiveLobbyService", ActiveLobbyService)
    monkeypatch.setattr(bot_module, "GuideThreadService", LeaderboardService)
    monkeypatch.setattr(bot_module, "DISCORD_COMMAND_GUILD_ID", 987654321)
    monkeypatch.setattr(
        bot_module.bot.tree,
        "copy_global_to",
        lambda *, guild: copied.append(guild.id),
    )
    monkeypatch.setattr(bot_module.bot.tree, "sync", sync)
    monkeypatch.setattr(bot_module.revalidate_tokens, "start", lambda: None)
    monkeypatch.setattr(
        bot_module.refresh_infrequently_changing_data,
        "start",
        lambda: None,
    )
    monkeypatch.setattr(
        bot_module.refresh_frequently_changing_data,
        "start",
        lambda: None,
    )
    monkeypatch.setattr(bot_module.publish_health, "start", lambda: None)
    monkeypatch.setattr(bot_module.bot, "leaderboard_service", None)
    monkeypatch.setattr(bot_module.bot, "active_lobby_service", None)
    monkeypatch.setattr(bot_module.bot, "guide_thread_service", None)

    await bot_module.setup_hook()

    assert copied == synced == [987654321]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("guild", "manage_guild", "expected"),
    [
        (False, True, "This command can only be used in a server."),
        (
            True,
            False,
            "You need the Manage Server permission to export debug data.",
        ),
    ],
)
async def test_debug_export_rejects_invalid_context_or_permissions(
    monkeypatch: pytest.MonkeyPatch,
    guild: bool,
    manage_guild: bool,
    expected: str,
) -> None:
    """Verify that debug exports reject DM contexts and insufficient permissions."""
    monkeypatch.setattr(
        bot_module.db,
        "get_guild_config",
        lambda guild_id: pytest.fail("database should not be queried"),
    )
    request = interaction(guild=guild, manage_guild=manage_guild)

    await bot_module.debug_get_db_data.callback(request)

    assert request.response.messages[0]["content"] == expected


@pytest.mark.asyncio
async def test_debug_export_requires_per_guild_enablement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that debug exports require per-guild enablement flag."""
    async def config(guild_id: int) -> dict[str, object]:
        """Return guild config with debug exports disabled."""
        return {"debug_commands_enabled": False}

    monkeypatch.setattr(bot_module.db, "get_guild_config", config)
    request = interaction()

    await bot_module.debug_get_db_data.callback(request)

    assert request.response.messages == [
        {
            "content": "Debug exports are disabled for this server.",
            "ephemeral": True,
        }
    ]


@pytest.mark.asyncio
async def test_debug_export_is_in_memory_and_attaches_three_datasets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Verify that debug exports attach three in-memory JSON datasets without writing to disk."""
    async def config(guild_id: int) -> dict[str, object]:
        """Return guild config with debug exports enabled."""
        return {"debug_commands_enabled": True}

    async def records() -> list[object]:
        """Return stub records for debug export."""
        return [SimpleNamespace(json=lambda: {"id": 1})]

    monkeypatch.setattr(bot_module.db, "get_guild_config", config)
    monkeypatch.setattr(bot_module.db, "get_users", records)
    monkeypatch.setattr(bot_module.db, "get_lobbies", records)
    monkeypatch.setattr(bot_module.db, "get_season_ratings", records)
    monkeypatch.chdir(tmp_path)
    request = interaction()

    await bot_module.debug_get_db_data.callback(request)

    assert request.response.deferred is True
    captured = request.followup.messages[0]["captured_files"]
    assert [name for name, _ in captured] == [
        "users.json",
        "lobbies.json",
        "ratings.json",
    ]
    assert all(b'"id": 1' in payload for _, payload in captured)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_debug_export_rejects_overlap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that concurrent debug exports are rejected to prevent resource contention."""
    first_started = asyncio.Event()
    finish_first = asyncio.Event()

    async def config(guild_id: int) -> dict[str, object]:
        """Return guild config with debug exports enabled."""
        return {"debug_commands_enabled": True}

    async def blocked_records() -> list[object]:
        """Block until signaled to simulate long-running export."""
        first_started.set()
        await finish_first.wait()
        return []

    async def empty_records() -> list[object]:
        """Return empty records immediately."""
        return []

    monkeypatch.setattr(bot_module.db, "get_guild_config", config)
    monkeypatch.setattr(bot_module.db, "get_users", blocked_records)
    monkeypatch.setattr(bot_module.db, "get_lobbies", empty_records)
    monkeypatch.setattr(bot_module.db, "get_season_ratings", empty_records)
    first_request = interaction()
    second_request = interaction()

    first_task = asyncio.create_task(
        bot_module.debug_get_db_data.callback(first_request)
    )
    await first_started.wait()
    await bot_module.debug_get_db_data.callback(second_request)
    finish_first.set()
    await first_task

    assert second_request.response.messages[0]["content"].startswith(
        "Another debug export"
    )


def test_debug_attachment_enforces_size_during_serialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that debug attachments reject data exceeding the size limit during serialization."""
    monkeypatch.setattr(bot_module, "MAX_DEBUG_ATTACHMENT_BYTES", 32)

    with pytest.raises(ValueError, match="safe size limit"):
        bot_module._json_attachment("large.json", [{"value": "x" * 100}])


@pytest.mark.asyncio
async def test_debug_attachment_cleanup_covers_partial_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that debug export cleanup closes partially constructed attachments on failure."""
    first_buffer = BytesIO(b"{}")
    calls = 0

    def make_attachment(
        name: str,
        records: list[object],
    ) -> tuple[BytesIO, discord.File]:
        """Create an attachment or raise an error on second call."""
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ValueError("too large")
        return first_buffer, discord.File(first_buffer, filename=name)

    monkeypatch.setattr(bot_module, "_json_attachment", make_attachment)

    with pytest.raises(ValueError, match="too large"):
        await bot_module._send_debug_export(
            interaction(),
            ([], [], []),
        )

    assert first_buffer.closed is True


def test_protected_command_decorators_match_runtime_policy() -> None:
    """Verify that protected command decorators enforce guild-only and permission requirements."""
    assert bot_module.create_roles_slash.guild_only is True
    assert bot_module.create_roles_slash.default_permissions.manage_roles is True
    for command in (
        bot_module.debug_get_db_data,
        bot_module.debug_get_battlevive_data,
        bot_module.refresh,
    ):
        assert command.guild_only is True
        assert command.default_permissions.manage_guild is True
    assert bot_module.config_debug.guild_only is True
    assert bot_module.config_debug.default_permissions.manage_guild is True
    assert bot_module.config_active_lobbies_moderator_role.guild_only is True
    assert (
        bot_module.config_active_lobbies_moderator_role.default_permissions.manage_guild
        is True
    )


@pytest.mark.asyncio
async def test_create_roles_runtime_policy_rejects_dm_and_missing_permission() -> None:
    """Verify that create_roles rejects DMs and requests without Manage Roles permission."""
    dm_request = interaction(guild=False)
    await bot_module.create_roles_slash.callback(dm_request)
    assert dm_request.response.messages[0]["content"].startswith(
        "This command can only"
    )

    unauthorized = interaction(manage_roles=False)
    await bot_module.create_roles_slash.callback(unauthorized)
    assert unauthorized.response.messages[0]["content"].startswith(
        "You need the Manage Roles"
    )

    bot_unauthorized = interaction()
    bot_unauthorized.guild.me.guild_permissions.manage_roles = False
    await bot_module.create_roles_slash.callback(bot_unauthorized)
    assert bot_unauthorized.response.messages[0]["content"].startswith(
        "I need the Manage Roles"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("guild", "manage_guild", "expected"),
    [
        (False, True, "This command can only be used in a server."),
        (
            True,
            False,
            "You need the Manage Server permission to refresh Battlevive data.",
        ),
    ],
)
async def test_refresh_runtime_policy_rejects_invalid_callers(
    guild: bool,
    manage_guild: bool,
    expected: str,
) -> None:
    """Verify that refresh command rejects DMs and requests without Manage Server permission."""
    request = interaction(guild=guild, manage_guild=manage_guild)

    await bot_module.refresh.callback(request)

    assert request.response.messages[0]["content"] == expected


def test_message_content_intent_and_prefix_processing_are_disabled() -> None:
    """Verify that the bot does not use message content intent or prefix commands."""
    assert bot_module.bot.intents.message_content is False
    assert "on_message" not in bot_module.bot.extra_events
    assert bot_module.bot.command_prefix == ()


def test_application_logs_only_to_stdout() -> None:
    """Verify that application loggers write only to stdout, not to files."""
    for logger in (logs.logger, logs.discord_logger):
        assert logger.propagate is False
        assert all(
            not isinstance(handler, logging.FileHandler)
            for handler in logger.handlers
        )


@pytest.mark.parametrize(
    ("url", "api_key"),
    [
        ("http://supabase.test", "key"),
        ("https://user:password@supabase.test", "key"),
        ("not-a-url", "key"),
        ("https://supabase.test", ""),
    ],
)
def test_supabase_transport_rejects_unsafe_settings(
    url: str,
    api_key: str,
) -> None:
    """Verify that Supabase transport rejects invalid URLs and empty API keys."""
    with pytest.raises(ValueError):
        SupabaseTransport(url, api_key)

"""Tests for gateway configuration, commands, and publication permissions."""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import Mock
from unittest.mock import AsyncMock

import discord
import pytest

import battlevive_gateway.gateway as gateway
from battlevive_gateway.gateway import TEMPORARILY_UNAVAILABLE
from battlevive_gateway.gateway import command_guild_id
from battlevive_gateway.gateway import validate_settings
from battlevive_gateway.gateway_bot import bypasses_channel_rules
from battlevive_gateway.gateway_bot import can_publish_active_lobbies
from battlevive_gateway.gateway_bot import can_publish_guides
from battlevive_gateway.gateway_bot import can_publish_leaderboard
from battlevive_gateway.gateway_bot import create_bot


def test_unavailable_features_have_one_concise_response() -> None:
    """Verify that unavailable features have one concise response."""
    assert TEMPORARILY_UNAVAILABLE == "This feature is temporarily unavailable."


def test_gateway_exposes_a_configured_stdout_logger() -> None:
    """Verify that gateway exposes a configured stdout logger."""
    assert gateway.logger.name == "bot"


def test_administrator_diagnostics_bypass_channel_rules() -> None:
    """Verify that administrator diagnostics bypass channel rules."""
    assert bypasses_channel_rules("config guide-forum") is True
    assert bypasses_channel_rules("debug upstream") is True
    assert bypasses_channel_rules("refresh") is False


def test_publication_channel_validators_require_the_bot_permissions() -> None:
    """Verify that publication channel validators require the bot permissions."""
    guild = SimpleNamespace(me=object())
    permissions = SimpleNamespace(
        view_channel=True, send_messages=True, attach_files=True, embed_links=True, read_message_history=True,
        send_messages_in_threads=True, manage_threads=True,
    )
    text = Mock(spec=discord.TextChannel)
    text.permissions_for.return_value = permissions
    forum = Mock(spec=discord.ForumChannel)
    forum.permissions_for.return_value = permissions

    assert can_publish_leaderboard(guild, text)
    assert can_publish_active_lobbies(guild, text)
    assert can_publish_guides(guild, forum)
    permissions.embed_links = False
    assert not can_publish_active_lobbies(guild, text)
    permissions.embed_links = True
    permissions.attach_files = False
    assert not can_publish_leaderboard(guild, text)
    assert can_publish_active_lobbies(guild, text)


def test_gateway_rejects_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that gateway rejects API key."""
    for name, value in {
        "DISCORD_TOKEN": "token", "DATABASE_URL": "postgresql://example",
        "UPSTREAM_DATA_URL": "http://upstream", "IMAGE_RENDERER_URL": "http://renderer",
        "BATTLEVIVE_API_KEY": "not-allowed",
    }.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeError, match="must not receive"):
        validate_settings()


def test_development_guild_id_requires_positive_snowflake(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that development guild ID requires positive snowflake."""
    monkeypatch.setenv("DISCORD_COMMAND_GUILD_ID", "not-a-snowflake")
    with pytest.raises(RuntimeError, match="positive snowflake"):
        command_guild_id()


def test_gateway_keeps_incomplete_commands_registered_as_unavailable() -> None:
    """Verify that gateway keeps incomplete commands registered as unavailable."""
    bot = create_bot()
    names = {command.name for command in bot.tree.get_commands()}
    assert {"refresh", "rank", "create_roles", "config", "debug"} <= names
    assert "leaderboard" not in names
    assert "active_lobbies" not in names
    assert "captains" not in names
    config = next(command for command in bot.tree.get_commands() if command.name == "config")
    assert {"rank-cooldown", "show", "command-whitelist", "command-blacklist", "active-lobby-channel"} <= {
        command.name for command in config.commands
    }
    commands_group = next(command for command in config.commands if command.name == "commands")
    assert {"whitelist", "blacklist", "remove"} == {
        command.name for command in commands_group.commands
    }


@pytest.mark.asyncio
async def test_create_roles_uses_the_latest_config_and_handles_a_concurrent_link_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that create roles uses the latest config and handles a concurrent link update."""
    import battlevive_gateway.gateway_bot as gateway_bot

    class Member:
        """Provide a member test double."""
        guild_permissions = SimpleNamespace(manage_roles=True)
        id = 99

    class Connection:
        """Provide a connection test double."""
        def __init__(self, version: int) -> None:
            """Initialize the connection instance."""
            self.version = version
            self.update_versions: list[int] = []

        async def fetchrow(self, query: str, *_: object) -> dict[str, int] | None:
            """Provide fetchrow behavior for the test scenario."""
            return {"version": self.version, "guide_notification_role_id": None} if "SELECT" in query else None

        async def execute(self, query: str, *args: object) -> str:
            """Provide execute behavior for the test scenario."""
            if query.startswith("UPDATE guild_config"):
                self.update_versions.append(int(args[1]))
                return "UPDATE 0"
            return "INSERT 0 1"

    class Acquire:
        """Provide a acquire test double."""
        def __init__(self, connection: Connection) -> None:
            """Initialize the acquire instance."""
            self.connection = connection

        async def __aenter__(self) -> Connection:
            """Enter the asynchronous context manager."""
            return self.connection

        async def __aexit__(self, *_: object) -> None:
            """Exit the asynchronous context manager."""
            return None

    initial, current = Connection(1), Connection(2)
    connections = [initial, current]
    bot = create_bot()
    bot.pool = type("Pool", (), {"acquire": lambda _: Acquire(connections.pop(0))})()
    guide_role = SimpleNamespace(id=123, name="Guide Updates")
    guild = SimpleNamespace(id=7, roles=[guide_role])
    interaction = SimpleNamespace(
        guild=guild,
        user=Member(),
        response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )
    monkeypatch.setattr(gateway_bot.discord, "Member", Member)
    monkeypatch.setattr(gateway_bot, "create_required_roles", AsyncMock(return_value=([], [], [])))

    command = next(command for command in bot.tree.get_commands() if command.name == "create_roles")
    await command.callback(interaction)

    assert current.update_versions == [2]
    interaction.followup.send.assert_awaited_once_with("No roles changed.", ephemeral=True)


@pytest.mark.asyncio
async def test_refresh_defers_before_running_integrations() -> None:
    """Guide synchronization can exceed Discord's three-second initial response window."""
    bot = create_bot()
    refresh = next(command for command in bot.tree.get_commands() if command.name == "refresh")

    interaction = type("Interaction", (), {
        "guild_id": 1,
        "user": type("Member", (), {"guild_permissions": type("Permissions", (), {"manage_guild": True})()})(),
        "response": type("Response", (), {"defer": AsyncMock()})(),
        "followup": type("Followup", (), {"send": AsyncMock()})(),
    })()

    await refresh.callback(interaction)

    interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
    interaction.followup.send.assert_awaited_once_with("No integrations configured.", ephemeral=True)


@pytest.mark.asyncio
async def test_refresh_requires_manage_server_before_starting_work() -> None:
    """Verify that refresh requires manage server before starting work."""
    bot = create_bot()
    refresh = next(command for command in bot.tree.get_commands() if command.name == "refresh")
    interaction = type("Interaction", (), {
        "guild_id": 1,
        "user": type("Member", (), {"guild_permissions": type("Permissions", (), {"manage_guild": False})()})(),
        "response": type("Response", (), {"defer": AsyncMock(), "send_message": AsyncMock()})(),
    })()

    await refresh.callback(interaction)

    interaction.response.defer.assert_not_awaited()
    interaction.response.send_message.assert_awaited_once_with(  # type: ignore[attr-defined]
        "Manage Server permission is required.", ephemeral=True
    )


@pytest.mark.asyncio
async def test_refresh_enforces_a_ten_second_cooldown_per_guild(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that refresh enforces a ten second cooldown per guild."""
    bot = create_bot()
    refresh = next(command for command in bot.tree.get_commands() if command.name == "refresh")
    monkeypatch.setattr("battlevive_gateway.gateway_bot.time.monotonic", lambda: 100.0)

    def interaction(guild_id: int) -> object:
        """Provide interaction behavior for the test scenario."""
        return type("Interaction", (), {
            "guild_id": guild_id,
            "user": type("Member", (), {"guild_permissions": type("Permissions", (), {"manage_guild": True})()})(),
            "response": type("Response", (), {
                "defer": AsyncMock(),
                "send_message": AsyncMock(),
            })(),
            "followup": type("Followup", (), {"send": AsyncMock()})(),
        })()

    first, repeated, other_guild = interaction(1), interaction(1), interaction(2)

    await refresh.callback(first)
    await refresh.callback(repeated)
    await refresh.callback(other_guild)

    first.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)  # type: ignore[attr-defined]
    repeated.response.defer.assert_not_awaited()  # type: ignore[attr-defined]
    repeated.response.send_message.assert_awaited_once_with(  # type: ignore[attr-defined]
        "Please wait before refreshing this server again.", ephemeral=True
    )
    other_guild.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_rank_uses_saved_member_number_and_renderer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that rank uses saved member number and renderer."""
    bot = create_bot()
    rank = next(command for command in bot.tree.get_commands() if command.name == "rank")

    class Connection:
        """Provide a connection test double."""
        async def fetchrow(self, query: str, guild_id: int) -> dict[str, int]:
            """Provide fetchrow behavior for the test scenario."""
            assert "guild_config" in query and guild_id == 1
            return {"rank_cooldown_seconds": 0}

        async def fetchval(self, query: str, discord_id: int) -> int:
            """Provide fetchval behavior for the test scenario."""
            assert "identity_links" in query and discord_id == 99
            return 7

    class Acquire:
        """Provide a acquire test double."""
        async def __aenter__(self) -> Connection:
            """Enter the asynchronous context manager."""
            return Connection()
        async def __aexit__(self, *_: object) -> None:
            """Exit the asynchronous context manager."""
            return None

    bot.pool = type("Pool", (), {"acquire": lambda _: Acquire()})()
    calls: list[str] = []

    class Upstream:
        """Provide a upstream test double."""
        async def get_result(self, path: str, *, require_fresh: bool) -> object:
            """Provide get result behavior for the test scenario."""
            calls.append(path)
            return type("Result", (), {"data": {"player": {"name": "Alpha", "mmr": 2000, "rank": "Gold", "wins": 4, "losses": 1}}})()

    class Renderer:
        """Provide a renderer test double."""
        async def render_rank(self, model: dict[str, object]) -> bytes:
            """Provide render rank behavior for the test scenario."""
            assert model["rank_next"] == "Platinum"
            return b"\x89PNG\r\n\x1a\n"

    bot.upstream, bot.renderer = Upstream(), Renderer()
    avatar = type("Avatar", (), {"with_size": lambda _, __: type("Sized", (), {"read": AsyncMock(return_value=b"avatar")})()})()
    interaction = type("Interaction", (), {
        "guild_id": 1, "user": type("User", (), {"id": 99, "display_avatar": avatar})(),
        "response": type("Response", (), {"defer": AsyncMock()})(),
        "followup": type("Followup", (), {"send": AsyncMock()})(),
    })()

    await rank.callback(interaction)

    assert calls == ["/players/7"]
    interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
    assert interaction.followup.send.await_args.kwargs["file"].filename == "profile.png"


@pytest.mark.asyncio
async def test_rank_enforces_the_configured_guild_cooldown() -> None:
    """Verify that rank enforces the configured guild cooldown."""
    bot = create_bot()
    rank = next(command for command in bot.tree.get_commands() if command.name == "rank")

    class Connection:
        """Provide a connection test double."""
        async def fetchrow(self, query: str, guild_id: int) -> dict[str, int]:
            """Provide fetchrow behavior for the test scenario."""
            assert "guild_config" in query and guild_id == 1
            return {"rank_cooldown_seconds": 30}

    class Acquire:
        """Provide a acquire test double."""
        async def __aenter__(self) -> Connection:
            """Enter the asynchronous context manager."""
            return Connection()
        async def __aexit__(self, *_: object) -> None:
            """Exit the asynchronous context manager."""
            return None

    bot.pool = type("Pool", (), {"acquire": lambda _: Acquire()})()
    bot.upstream, bot.renderer = object(), object()
    bot.rank_cooldowns[(1, 99)] = float("inf")
    interaction = type("Interaction", (), {
        "guild_id": 1, "user": type("User", (), {"id": 99})(),
        "response": type("Response", (), {"send_message": AsyncMock()})(),
    })()

    await rank.callback(interaction)

    interaction.response.send_message.assert_awaited_once_with(
        "Please wait before using /rank again.", ephemeral=True
    )

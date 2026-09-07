from __future__ import annotations

import os
from unittest.mock import AsyncMock

import pytest

import battlevive_gateway.gateway as gateway
from battlevive_gateway.gateway import TEMPORARILY_UNAVAILABLE
from battlevive_gateway.gateway import command_guild_id
from battlevive_gateway.gateway import validate_settings
from battlevive_gateway.gateway_bot import bypasses_channel_rules
from battlevive_gateway.gateway_bot import create_bot


def test_unavailable_features_have_one_concise_response() -> None:
    assert TEMPORARILY_UNAVAILABLE == "This feature is temporarily unavailable."


def test_gateway_exposes_a_configured_stdout_logger() -> None:
    assert gateway.logger.name == "bot"


def test_administrator_diagnostics_bypass_channel_rules() -> None:
    assert bypasses_channel_rules("config guide-forum") is True
    assert bypasses_channel_rules("debug upstream") is True
    assert bypasses_channel_rules("refresh") is False


def test_gateway_rejects_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in {
        "DISCORD_TOKEN": "token", "DATABASE_URL": "postgresql://example",
        "UPSTREAM_DATA_URL": "http://upstream", "IMAGE_RENDERER_URL": "http://renderer",
        "BATTLEVIVE_API_KEY": "not-allowed",
    }.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeError, match="must not receive"):
        validate_settings()


def test_development_guild_id_requires_positive_snowflake(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_COMMAND_GUILD_ID", "not-a-snowflake")
    with pytest.raises(RuntimeError, match="positive snowflake"):
        command_guild_id()


def test_gateway_keeps_incomplete_commands_registered_as_unavailable() -> None:
    bot = create_bot()
    names = {command.name for command in bot.tree.get_commands()}
    assert {"refresh", "rank", "create_roles", "config", "debug"} <= names
    assert "leaderboard" not in names
    assert "active_lobbies" not in names
    assert "captains" not in names
    config = next(command for command in bot.tree.get_commands() if command.name == "config")
    assert {"rank-cooldown", "show", "command-whitelist", "command-blacklist"} <= {
        command.name for command in config.commands
    }
    commands_group = next(command for command in config.commands if command.name == "commands")
    assert {"whitelist", "blacklist", "remove"} == {
        command.name for command in commands_group.commands
    }


@pytest.mark.asyncio
async def test_refresh_defers_before_running_integrations() -> None:
    """Guide synchronization can exceed Discord's three-second initial response window."""
    bot = create_bot()
    refresh = next(command for command in bot.tree.get_commands() if command.name == "refresh")

    interaction = type("Interaction", (), {
        "guild_id": None,
        "response": type("Response", (), {"defer": AsyncMock()})(),
        "followup": type("Followup", (), {"send": AsyncMock()})(),
    })()

    await refresh.callback(interaction)

    interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
    interaction.followup.send.assert_awaited_once_with("No integrations configured.", ephemeral=True)


@pytest.mark.asyncio
async def test_rank_uses_saved_member_number_and_renderer(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = create_bot()
    rank = next(command for command in bot.tree.get_commands() if command.name == "rank")

    class Connection:
        async def fetchrow(self, query: str, guild_id: int) -> dict[str, int]:
            assert "guild_config" in query and guild_id == 1
            return {"rank_cooldown_seconds": 0}

        async def fetchval(self, query: str, discord_id: int) -> int:
            assert "identity_links" in query and discord_id == 99
            return 7

    class Acquire:
        async def __aenter__(self) -> Connection: return Connection()
        async def __aexit__(self, *_: object) -> None: return None

    bot.pool = type("Pool", (), {"acquire": lambda _: Acquire()})()
    calls: list[str] = []

    class Upstream:
        async def get_result(self, path: str, *, require_fresh: bool) -> object:
            calls.append(path)
            return type("Result", (), {"data": {"player": {"name": "Alpha", "mmr": 2000, "rank": "Gold", "wins": 4, "losses": 1}}})()

    class Renderer:
        async def render_rank(self, model: dict[str, object]) -> bytes:
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
    bot = create_bot()
    rank = next(command for command in bot.tree.get_commands() if command.name == "rank")

    class Connection:
        async def fetchrow(self, query: str, guild_id: int) -> dict[str, int]:
            assert "guild_config" in query and guild_id == 1
            return {"rank_cooldown_seconds": 30}

    class Acquire:
        async def __aenter__(self) -> Connection: return Connection()
        async def __aexit__(self, *_: object) -> None: return None

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

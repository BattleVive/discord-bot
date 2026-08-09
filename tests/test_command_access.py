from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("discord")
pytest.importorskip("asyncpg")

from battlevive_bot.command_access import CommandAccessService


class Response:
    def __init__(self) -> None:
        self.messages: list[tuple[str, bool]] = []

    async def send_message(self, message: str, *, ephemeral: bool) -> None:
        self.messages.append((message, ephemeral))


def interaction(*, guild_id: int = 1, user_id: int = 2, channel_id: int = 3, parent_id=None):
    channel = SimpleNamespace(id=channel_id, parent_id=parent_id)
    guild = SimpleNamespace(
        id=guild_id,
        get_channel=lambda requested: channel if requested == channel_id else None,
    )
    return SimpleNamespace(
        guild=guild,
        channel=channel,
        channel_id=channel_id,
        user=SimpleNamespace(id=user_id),
        response=Response(),
    )


@pytest.mark.asyncio
async def test_denied_channel_does_not_consume_cooldown(monkeypatch) -> None:
    service = CommandAccessService()
    command = SimpleNamespace(extras={"public": True, "cooldown_setting": "rank"})
    rules = [{"channel_id": 3, "rule": "block"}]

    async def get_rules(guild_id: int):
        return list(rules)

    async def get_cooldown(guild_id: int):
        return 20

    monkeypatch.setattr("battlevive_bot.command_access.db.get_command_channel_rules", get_rules)
    monkeypatch.setattr(
        "battlevive_bot.command_access.db.get_rank_cooldown_seconds",
        get_cooldown,
    )
    monkeypatch.setattr("battlevive_bot.command_access.db.remove_command_channel_rule", lambda *args: None)
    denied = interaction()
    assert not await service.check(denied, command)
    rules.clear()
    accepted = interaction()
    assert await service.check(accepted, command)


@pytest.mark.asyncio
async def test_default_cooldown_is_per_user_and_guild(monkeypatch) -> None:
    service = CommandAccessService()
    command = SimpleNamespace(extras={"public": True, "cooldown_setting": "rank"})

    async def no_rules(guild_id: int):
        return []

    async def default_cooldown(guild_id: int):
        return 20

    monkeypatch.setattr("battlevive_bot.command_access.db.get_command_channel_rules", no_rules)
    monkeypatch.setattr(
        "battlevive_bot.command_access.db.get_rank_cooldown_seconds",
        default_cooldown,
    )
    assert await service.check(interaction(), command)
    blocked = interaction()
    assert not await service.check(blocked, command)
    assert "20 seconds" in blocked.response.messages[0][0]
    assert await service.check(interaction(user_id=9), command)
    assert await service.check(interaction(guild_id=8), command)


@pytest.mark.asyncio
async def test_thread_inherits_parent_allow_rule(monkeypatch) -> None:
    service = CommandAccessService()
    command = SimpleNamespace(extras={"public": True, "cooldown_setting": "rank"})
    parent = SimpleNamespace(id=10)
    thread = interaction(channel_id=11, parent_id=10)
    thread.guild.get_channel = lambda requested: parent if requested == 10 else None

    async def rules(guild_id: int):
        return [{"channel_id": 10, "rule": "allow"}]

    async def disabled(guild_id: int):
        return 0

    monkeypatch.setattr("battlevive_bot.command_access.db.get_command_channel_rules", rules)
    monkeypatch.setattr(
        "battlevive_bot.command_access.db.get_rank_cooldown_seconds",
        disabled,
    )
    assert await service.check(thread, command)


@pytest.mark.asyncio
async def test_staff_command_is_exempt() -> None:
    service = CommandAccessService()
    command = SimpleNamespace(extras={})
    assert await service.check(interaction(), command)

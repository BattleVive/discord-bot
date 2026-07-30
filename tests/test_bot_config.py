from __future__ import annotations

from types import SimpleNamespace

import pytest

discord = pytest.importorskip("discord")
pytest.importorskip("asyncpg")

from battlevive_bot import bot as bot_module


def test_config_channel_exposes_text_and_news_channel_types() -> None:
    parameter = bot_module.config_leaderboard_channel.get_parameter("channel")

    assert parameter is not None
    assert parameter.channel_types == [
        discord.ChannelType.text,
        discord.ChannelType.news,
    ]


class FakeResponse:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    async def send_message(self, content: str, **kwargs: object) -> None:
        self.messages.append({"content": content, **kwargs})

    def is_done(self) -> bool:
        return bool(self.messages)


class FakeChannel:
    def __init__(
        self,
        *,
        view: bool = True,
        send: bool = True,
        attach: bool = True,
        history: bool = True,
        channel_type: discord.ChannelType = discord.ChannelType.text,
    ) -> None:
        self.id = 456
        self.name = "leaderboard"
        self.type = channel_type
        self._permissions = SimpleNamespace(
            view_channel=view,
            send_messages=send,
            attach_files=attach,
            read_message_history=history,
        )

    def permissions_for(self, member: object) -> SimpleNamespace:
        return self._permissions

    def __str__(self) -> str:
        return f"#{self.name}"


def make_interaction(
    *,
    manage_guild: bool = True,
    channel: FakeChannel | None = None,
) -> SimpleNamespace:
    user = SimpleNamespace(
        id=123,
        guild_permissions=SimpleNamespace(manage_guild=manage_guild),
    )
    guild = SimpleNamespace(id=789, me=object())
    return SimpleNamespace(
        user=user,
        guild=guild,
        response=FakeResponse(),
        channel=channel,
    )


@pytest.mark.asyncio
async def test_config_channel_requires_manage_guild() -> None:
    interaction = make_interaction(manage_guild=False)

    await bot_module.config_leaderboard_channel.callback(
        interaction,
        FakeChannel(),
    )

    assert interaction.response.messages == [
        {
            "content": "You need the Manage Server permission to change bot configuration.",
            "ephemeral": True,
        }
    ]


@pytest.mark.asyncio
async def test_config_channel_validates_bot_permissions_and_stores_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interaction = make_interaction(channel=FakeChannel())
    calls: list[tuple[int, int, int]] = []

    async def fake_upsert(guild_id: int, channel_id: int, updated_by: int) -> None:
        calls.append((guild_id, channel_id, updated_by))

    monkeypatch.setattr(bot_module.db, "upsert_guild_config", fake_upsert)

    await bot_module.config_leaderboard_channel.callback(interaction, interaction.channel)

    assert calls == [(789, 456, 123)]
    assert interaction.response.messages == [
        {
            "content": "Leaderboard channel set to #leaderboard.",
            "ephemeral": True,
        }
    ]


@pytest.mark.asyncio
async def test_config_channel_rejects_missing_bot_permissions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interaction = make_interaction(channel=FakeChannel(send=False))
    upsert = False

    async def fake_upsert(*args: object) -> None:
        nonlocal upsert
        upsert = True

    monkeypatch.setattr(bot_module.db, "upsert_guild_config", fake_upsert)

    await bot_module.config_leaderboard_channel.callback(interaction, interaction.channel)

    assert not upsert
    assert interaction.response.messages == [
        {
            "content": "I need View Channel, Send Messages, Attach Files, and Read Message History "
            "permissions in that channel.",
            "ephemeral": True,
        }
    ]


@pytest.mark.asyncio
async def test_config_channel_rejects_non_text_channels() -> None:
    interaction = make_interaction()

    await bot_module.config_leaderboard_channel.callback(
        interaction,
        FakeChannel(channel_type=discord.ChannelType.voice),
    )

    assert interaction.response.messages == [
        {"content": "Please choose a text or news channel.", "ephemeral": True}
    ]


@pytest.mark.asyncio
async def test_config_reset_and_show_are_ephemeral(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_calls: list[tuple[int, int]] = []

    async def fake_reset(guild_id: int, updated_by: int) -> None:
        reset_calls.append((guild_id, updated_by))

    async def fake_get(guild_id: int) -> dict[str, int | None]:
        assert guild_id == 789
        return {
            "guild_id": guild_id,
            "leaderboard_channel_id": 456,
            "leaderboard_limit": None,
            "debug_commands_enabled": False,
            "updated_by": 123,
        }

    monkeypatch.setattr(bot_module.db, "reset_guild_config", fake_reset)
    monkeypatch.setattr(bot_module.db, "get_guild_config", fake_get)

    reset_interaction = make_interaction()
    show_interaction = make_interaction()
    await bot_module.config_reset_leaderboard.callback(reset_interaction)
    await bot_module.config_show.callback(show_interaction)

    assert reset_calls == [(789, 123)]
    assert reset_interaction.response.messages == [
        {"content": "Leaderboard configuration reset.", "ephemeral": True}
    ]
    assert show_interaction.response.messages == [
        {
            "content": "Leaderboard channel: <#456>.\nLeaderboard limit: 50 (maximum).\nDebug exports: disabled.",
            "ephemeral": True,
        }
    ]


@pytest.mark.asyncio
async def test_config_limit_sets_amount_and_omission_restores_the_maximum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[int, int | None, int]] = []

    async def fake_set(guild_id: int, amount: int | None, updated_by: int) -> None:
        calls.append((guild_id, amount, updated_by))

    async def fake_get(guild_id: int) -> dict[str, int | None]:
        return {
            "guild_id": guild_id,
            "leaderboard_channel_id": None,
            "leaderboard_limit": None,
            "debug_commands_enabled": False,
            "updated_by": 123,
        }

    monkeypatch.setattr(bot_module.db, "set_leaderboard_limit", fake_set)
    monkeypatch.setattr(bot_module.db, "get_guild_config", fake_get)

    limited = make_interaction()
    unlimited = make_interaction()
    await bot_module.config_leaderboard_limit.callback(limited, 25)
    await bot_module.config_leaderboard_limit.callback(unlimited, None)

    assert calls == [(789, 25, 123), (789, None, 123)]
    assert limited.response.messages[0]["content"] == "Leaderboard limit set to 25."
    assert unlimited.response.messages[0]["content"] == "Leaderboard limit set to 50 (maximum)."


@pytest.mark.asyncio
async def test_config_debug_updates_only_the_current_guild(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[int, bool, int]] = []

    async def fake_set(guild_id: int, enabled: bool, updated_by: int) -> None:
        calls.append((guild_id, enabled, updated_by))

    monkeypatch.setattr(bot_module.db, "set_debug_commands_enabled", fake_set)
    interaction = make_interaction()

    await bot_module.config_debug.callback(interaction, True)

    assert calls == [(789, True, 123)]
    assert interaction.response.messages == [
        {
            "content": "Debug exports enabled for this server.",
            "ephemeral": True,
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("amount", [0, 51])
async def test_config_limit_rejects_values_outside_the_supported_range(amount: int) -> None:
    interaction = make_interaction()

    await bot_module.config_leaderboard_limit.callback(interaction, amount)

    assert interaction.response.messages == [
        {
            "content": "Leaderboard limit must be between 1 and 50.",
            "ephemeral": True,
        }
    ]


@pytest.mark.asyncio
async def test_config_command_returns_ephemeral_error_when_database_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interaction = make_interaction()

    async def fail_get(guild_id: int) -> None:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(bot_module.db, "get_guild_config", fail_get)

    await bot_module.config_show.callback(interaction)

    assert interaction.response.messages == [
        {
            "content": "The command failed. Please try again later.",
            "ephemeral": True,
        }
    ]

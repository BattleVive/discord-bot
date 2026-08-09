from __future__ import annotations

from types import SimpleNamespace

import pytest

discord = pytest.importorskip("discord")
pytest.importorskip("asyncpg")

from battlevive_bot import bot as bot_module
from battlevive_bot import roles as roles_module


@pytest.fixture(autouse=True)
def default_new_database_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    async def config(guild_id: int) -> dict[str, object]:
        return {}

    async def rules(guild_id: int) -> list[dict[str, object]]:
        return []

    async def created(guild_id: int, purpose: str) -> None:
        return None

    async def ensure(guild_id: int, updated_by: int) -> None:
        return None

    monkeypatch.setattr(bot_module.db, "get_guild_config", config)
    monkeypatch.setattr(bot_module.db, "get_command_channel_rules", rules)
    monkeypatch.setattr(bot_module.db, "get_created_role", created)
    monkeypatch.setattr(bot_module.db, "ensure_guild_config", ensure)


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
        embed: bool = True,
        mention: bool = True,
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
            embed_links=embed,
            mention_everyone=mention,
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
            "content": "Leaderboard channel: <#456>.\nLeaderboard limit: 50 (maximum).\n"
            "Active-lobby channel: not configured.\n"
            "Active-lobby notification role: not configured.\n"
            "Website moderator role: not configured.\n"
            "Rank cooldown: 20 seconds.\n"
            "Whitelisted channels: none.\n"
            "Blacklisted channels: none.\n"
            "Debug exports: disabled.",
            "ephemeral": True,
        }
    ]


@pytest.mark.asyncio
async def test_active_lobby_channel_requires_all_posting_permissions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interaction = make_interaction(channel=FakeChannel(mention=False))
    called = False

    async def fake_set(*args: object) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(
        bot_module.db,
        "set_active_lobby_channel",
        fake_set,
        raising=False,
    )

    await bot_module.config_active_lobbies_channel.callback(
        interaction,
        interaction.channel,
    )

    assert called is False
    assert "Mention @everyone" in interaction.response.messages[0]["content"]


@pytest.mark.asyncio
async def test_active_lobby_channel_and_reset_update_only_current_guild(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[object, ...]] = []

    async def fake_set(*args: object) -> None:
        calls.append(("set", *args))

    async def fake_reset(*args: object) -> None:
        calls.append(("reset", *args))

    monkeypatch.setattr(
        bot_module.db,
        "set_active_lobby_channel",
        fake_set,
        raising=False,
    )
    monkeypatch.setattr(
        bot_module.db,
        "reset_active_lobby_config",
        fake_reset,
        raising=False,
    )
    monkeypatch.setattr(bot_module.bot, "active_lobby_service", None)
    channel_interaction = make_interaction(channel=FakeChannel())
    reset_interaction = make_interaction()

    await bot_module.config_active_lobbies_channel.callback(
        channel_interaction,
        channel_interaction.channel,
    )
    await bot_module.config_reset_active_lobbies.callback(reset_interaction)

    assert calls == [("set", 789, 456, 123), ("reset", 789, 123)]
    assert channel_interaction.response.messages[0]["ephemeral"] is True
    assert reset_interaction.response.messages[0]["ephemeral"] is True


@pytest.mark.asyncio
async def test_active_lobby_role_rejects_default_and_restores_generated_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    default = SimpleNamespace(id=1, name="@everyone", managed=False)
    default.is_default = lambda: True
    generated = SimpleNamespace(
        id=2,
        name="Active Lobby",
        managed=False,
        mention="<@&2>",
    )
    generated.is_default = lambda: False
    calls: list[tuple[int, int | None, int]] = []

    async def fake_set(guild_id: int, role_id: int | None, updated_by: int) -> None:
        calls.append((guild_id, role_id, updated_by))

    monkeypatch.setattr(
        bot_module.db,
        "set_active_lobby_role",
        fake_set,
        raising=False,
    )
    monkeypatch.setattr(bot_module.bot, "active_lobby_service", None)
    rejected = make_interaction()
    rejected.guild.default_role = default
    rejected.guild.roles = [default, generated]
    restored = make_interaction()
    restored.guild.default_role = default
    restored.guild.roles = [default, generated]

    await bot_module.config_active_lobbies_role.callback(rejected, default)
    await bot_module.config_active_lobbies_role.callback(restored, None)

    assert calls == [(789, 2, 123)]
    assert "non-default" in rejected.response.messages[0]["content"]
    assert restored.response.messages[0]["content"].endswith("<@&2>.")


@pytest.mark.asyncio
async def test_website_moderator_role_uses_generated_default_and_requests_reconcile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generated = SimpleNamespace(
        id=3,
        name=roles_module.WEBSITE_MODERATOR_ROLE,
        managed=False,
        mention="<@&3>",
    )
    generated.is_default = lambda: False
    calls: list[tuple[int, int | None, int]] = []
    service = SimpleNamespace(request_reconciliation=lambda: calls.append((0, None, 0)))

    async def fake_set(guild_id: int, role_id: int | None, updated_by: int) -> None:
        calls.append((guild_id, role_id, updated_by))

    monkeypatch.setattr(bot_module.db, "set_website_moderator_role", fake_set)
    monkeypatch.setattr(bot_module.bot, "active_lobby_service", service)
    interaction = make_interaction()
    interaction.guild.default_role = SimpleNamespace(id=1, name="@everyone")
    interaction.guild.roles = [interaction.guild.default_role, generated]

    await bot_module.config_active_lobbies_moderator_role.callback(
        interaction,
        None,
    )

    assert calls == [(789, 3, 123), (0, None, 0)]
    assert interaction.response.messages[0]["content"].endswith("<@&3>.")


class CommandRole:
    def __init__(
        self,
        role_id: int,
        name: str,
        *,
        mentionable: bool = False,
        position: int = 1,
    ) -> None:
        self.id = role_id
        self.name = name
        self.mentionable = mentionable
        self.position = position
        self.managed = False
        self.permissions = SimpleNamespace(value=0)
        self.edit_result: CommandRole | None = None

    async def edit(self, **kwargs: object) -> "CommandRole":
        assert kwargs["mentionable"] is False
        assert self.edit_result is not None
        return self.edit_result

    def is_assignable(self) -> bool:
        return True

    def is_default(self) -> bool:
        return self.name == "@everyone"

    def __le__(self, other: "CommandRole") -> bool:
        return self.position <= other.position


def role_setup_interaction(
    roles: list[CommandRole],
    create_role: object,
) -> SimpleNamespace:
    default_role = CommandRole(1, "@everyone", position=0)
    bot_role = CommandRole(2, "Bot", position=100)
    bot_member = SimpleNamespace(
        guild_permissions=SimpleNamespace(manage_roles=True),
        top_role=bot_role,
    )
    guild = SimpleNamespace(
        id=789,
        name="Role Test Guild",
        roles=[default_role, *roles],
        default_role=default_role,
        me=bot_member,
        create_role=create_role,
    )
    user = SimpleNamespace(
        id=123,
        guild_permissions=SimpleNamespace(manage_roles=True),
    )
    return SimpleNamespace(guild=guild, user=user, response=FakeResponse())


def existing_setup_roles(active_role: CommandRole | None) -> list[CommandRole]:
    result = [
        CommandRole(index + 10, name)
        for index, name in enumerate(roles_module.REQUIRED_ROLE_NAMES)
        if name != roles_module.ACTIVE_LOBBY_ROLE
    ]
    if active_role is not None:
        result.append(active_role)
    return result


@pytest.mark.asyncio
async def test_create_roles_records_generated_ownership_before_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = {
        roles_module.ACTIVE_LOBBY_ROLE: CommandRole(
            61,
            roles_module.ACTIVE_LOBBY_ROLE,
        ),
        roles_module.WEBSITE_MODERATOR_ROLE: CommandRole(
            62,
            roles_module.WEBSITE_MODERATOR_ROLE,
        ),
    }

    async def create_role(**kwargs: object) -> CommandRole:
        return created[str(kwargs["name"])]

    existing = [
        CommandRole(index + 10, name)
        for index, name in enumerate(roles_module.REQUIRED_ROLE_NAMES)
        if name not in created
    ]
    interaction = role_setup_interaction(existing, create_role)
    events: list[tuple[object, ...]] = []

    async def get_config(guild_id: int) -> dict[str, object]:
        return {
            "active_lobby_role_id": None,
            "website_moderator_role_id": None,
        }

    async def set_active(guild_id: int, role_id: int, updated_by: int) -> None:
        events.append(("configure", "active_lobby", role_id))

    async def set_moderator(guild_id: int, role_id: int, updated_by: int) -> None:
        events.append(("configure", "website_moderator", role_id))

    async def record(guild_id: int, purpose: str, role_id: int, created_by: int) -> None:
        events.append(("own", purpose, role_id))

    monkeypatch.setattr(bot_module.db, "get_guild_config", get_config)
    monkeypatch.setattr(bot_module.db, "set_active_lobby_role", set_active)
    monkeypatch.setattr(bot_module.db, "set_website_moderator_role", set_moderator)
    monkeypatch.setattr(bot_module.db, "record_created_role", record)
    monkeypatch.setattr(bot_module.bot, "active_lobby_service", None)

    await bot_module.create_roles_slash.callback(interaction)

    assert events == [
        ("own", "active_lobby", 61),
        ("configure", "active_lobby", 61),
        ("own", "website_moderator", 62),
        ("configure", "website_moderator", 62),
    ]
    assert interaction.response.messages[0]["ephemeral"] is True


@pytest.mark.asyncio
async def test_create_roles_reuses_legacy_roles_without_adopting_ownership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = CommandRole(61, roles_module.ACTIVE_LOBBY_ROLE)
    moderator = CommandRole(62, roles_module.WEBSITE_MODERATOR_ROLE)

    async def unexpected_create(**kwargs: object) -> CommandRole:
        raise AssertionError("legacy roles should be reused")

    interaction = role_setup_interaction(
        [*existing_setup_roles(active), moderator], unexpected_create
    )
    configured: list[int] = []

    async def set_role(guild_id: int, role_id: int, updated_by: int) -> None:
        configured.append(role_id)

    async def record(*args: object) -> None:
        raise AssertionError("legacy roles must not be ownership-tracked")

    monkeypatch.setattr(bot_module.db, "set_active_lobby_role", set_role)
    monkeypatch.setattr(bot_module.db, "set_website_moderator_role", set_role)
    monkeypatch.setattr(bot_module.db, "record_created_role", record)
    monkeypatch.setattr(bot_module.bot, "active_lobby_service", None)
    await bot_module.create_roles_slash.callback(interaction)
    assert configured == [61, 62]


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

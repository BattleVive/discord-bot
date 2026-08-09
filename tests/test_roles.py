from __future__ import annotations

from types import SimpleNamespace

import pytest

discord = pytest.importorskip("discord")
pytest.importorskip("asyncpg")

from battlevive_bot import roles


def test_player_role_is_not_managed() -> None:
    assert "Battlevive Player" not in roles.REQUIRED_ROLE_NAMES
    assert not hasattr(roles, "BATTLEVIVE_PLAYER_ROLE")
    assert not hasattr(roles, "give_battlevive_role")


class FakeRole:
    def __init__(self, name: str, *, permissions: int = 0, position: int = 1) -> None:
        self.id = hash(name)
        self.name = name
        self.position = position
        self.permissions = SimpleNamespace(value=permissions)
        self.managed = False
        self.mentionable = False

    def is_assignable(self) -> bool:
        return True

    def __le__(self, other: "FakeRole") -> bool:
        return self.position <= other.position


class FakeMember:
    def __init__(self, member_id: int, name: str, display_name: str) -> None:
        self.id = member_id
        self.name = name
        self.display_name = display_name
        self.global_name = None
        self.nick = display_name
        self.roles: list[object] = []
        self.added: list[object] = []
        self.removed: list[object] = []

    async def add_roles(self, *assigned: object, **kwargs: object) -> None:
        self.added.extend(assigned)
        self.roles.extend(assigned)

    async def remove_roles(self, *removed: object, **kwargs: object) -> None:
        self.removed.extend(removed)
        self.roles = [role for role in self.roles if role not in removed]


def test_member_matching_is_case_insensitive_for_discord_nicknames() -> None:
    member = FakeMember(1234, "different_account_name", "Sunshies")
    assert roles._matching_member([member], "sunshies") is member


@pytest.mark.asyncio
async def test_create_roles_creates_only_unprivileged_mmr_tiers() -> None:
    top = FakeRole("Bot", position=10)
    everyone = FakeRole("@everyone", position=0)
    bot_member = SimpleNamespace(
        guild_permissions=SimpleNamespace(manage_roles=True), top_role=top
    )

    class Guild:
        id = 1
        name = "Guild"
        me = bot_member
        roles: list[FakeRole] = []
        default_role = everyone
        created: list[tuple[str, object, bool]] = []

        async def create_role(self, *, name, permissions, mentionable, reason):
            self.created.append((name, permissions, mentionable))
            role = FakeRole(name)
            self.roles.append(role)
            return role

    result = await roles.create_roles(Guild())
    assert result.created == list(roles.RANK_ROLE_NAMES_ORDERED)
    assert all(item[1].value == 0 and item[2] is False for item in Guild.created)
    assert "Active Lobby" not in result.created
    assert "Website Moderator" not in result.created


@pytest.mark.asyncio
async def test_rank_reconciliation_matches_unrated_users_before_stale_removal(
    monkeypatch,
) -> None:
    silver = FakeRole("Silver")
    active_unrated = FakeMember(10, "active", "Active")
    stale = FakeMember(20, "stale", "Stale")
    stale.roles = [silver]
    guild = SimpleNamespace(
        id=1,
        name="Guild",
        roles=[silver],
        members=[active_unrated, stale],
        me=SimpleNamespace(top_role=FakeRole("Bot", position=10)),
    )

    class Pool:
        async def fetch(self, query):
            return [{"id": "a", "discord_id": 10, "discord_username": "active", "mmr": None}]

    async def resolve(target_guild, user):
        return active_unrated, True

    monkeypatch.setattr(roles.db, "get_pool", lambda: Pool())
    monkeypatch.setattr(roles, "resolve_user_in_guild", resolve)
    await roles.give_rank_roles(SimpleNamespace(guilds=[guild]))
    assert active_unrated.removed == []
    assert stale.removed == [silver]


@pytest.mark.asyncio
async def test_incomplete_resolution_skips_destructive_stale_removal(monkeypatch) -> None:
    silver = FakeRole("Silver")
    stale = FakeMember(20, "stale", "Stale")
    stale.roles = [silver]
    guild = SimpleNamespace(
        id=1,
        name="Guild",
        roles=[silver],
        members=[stale],
        me=SimpleNamespace(top_role=FakeRole("Bot", position=10)),
    )

    class Pool:
        async def fetch(self, query):
            return [{"id": "a", "discord_id": 10, "discord_username": "active", "mmr": 1500}]

    async def unresolved(target_guild, user):
        return None, False

    monkeypatch.setattr(roles.db, "get_pool", lambda: Pool())
    monkeypatch.setattr(roles, "resolve_user_in_guild", unresolved)
    await roles.give_rank_roles(SimpleNamespace(guilds=[guild]))
    assert stale.removed == []

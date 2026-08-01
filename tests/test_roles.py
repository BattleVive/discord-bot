from __future__ import annotations

from types import SimpleNamespace

import pytest

discord = pytest.importorskip("discord")
pytest.importorskip("asyncpg")

from battlevive_bot import roles


def test_active_lobby_role_is_part_of_unprivileged_role_setup() -> None:
    assert roles.ACTIVE_LOBBY_ROLE in roles.REQUIRED_ROLE_NAMES
    assert roles.WEBSITE_MODERATOR_ROLE in roles.REQUIRED_ROLE_NAMES


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

    async def add_roles(self, *assigned: object) -> None:
        self.added.extend(assigned)
        self.roles.extend(assigned)

    async def remove_roles(self, *removed: object) -> None:
        self.removed.extend(removed)
        self.roles = [role for role in self.roles if role not in removed]

    def __str__(self) -> str:
        return self.name


class FakeGuild:
    def __init__(self, member: FakeMember, guild_roles: list[object] | None = None) -> None:
        self.id = 100
        self.name = "Test Guild"
        self.me = SimpleNamespace(
            top_role=SimpleNamespace(position=100),
        )
        self.members = [member]
        self.roles = guild_roles or []
        self.member = member
        self.queries: list[str] = []

    def get_member(self, member_id: int) -> FakeMember | None:
        return self.member if member_id == self.member.id else None

    async def fetch_member(self, member_id: int) -> FakeMember:
        raise AssertionError("cached member should be used")

    async def query_members(self, *, query: str, limit: int) -> list[FakeMember]:
        self.queries.append(query)
        return [self.member]


def test_member_matching_is_case_insensitive_for_discord_nicknames() -> None:
    sunshies = FakeMember(1234, "different_account_name", "Sunshies")

    member = roles._matching_member([sunshies], "sunshies")

    assert member is sunshies


@pytest.mark.asyncio
async def test_rank_sync_links_case_insensitive_member_and_assigns_rank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    silver = SimpleNamespace(name="Silver")
    member = FakeMember(1234, "different_account_name", "Sunshies")
    guild = FakeGuild(member, [silver])
    links: list[tuple[str, int]] = []

    async def link(user_id: str, discord_id: int) -> bool:
        links.append((str(user_id), discord_id))
        return True

    class FakePool:
        async def fetch(self, query: str) -> list[dict[str, object]]:
            assert "WITH current_season" in query
            return [
                {
                    "id": "aa339e7a-19d0-4ce5-be96-ab852ad4b6be",
                    "discord_id": None,
                    "discord_username": "sunshies",
                    "mmr": 1500,
                }
            ]

    monkeypatch.setattr(roles.db, "set_user_discord_id", link)
    monkeypatch.setattr(roles, "get_pool", lambda: FakePool())

    bot = SimpleNamespace(guilds=[guild])
    await roles.give_rank_roles(bot)

    assert links == [("aa339e7a-19d0-4ce5-be96-ab852ad4b6be", 1234)]
    assert member.added == [silver]


class FakeRole:
    def __init__(
        self,
        name: str,
        *,
        position: int = 1,
        permissions: int = 0,
        managed: bool = False,
        mentionable: bool = False,
    ) -> None:
        self.id = hash(name)
        self.name = name
        self.position = position
        self.permissions = SimpleNamespace(value=permissions)
        self.managed = managed
        self.mentionable = mentionable
        self.edits: list[dict[str, object]] = []

    async def edit(self, **kwargs: object) -> "FakeRole":
        self.edits.append(kwargs)
        if "mentionable" in kwargs:
            self.mentionable = bool(kwargs["mentionable"])
        return self

    def is_assignable(self) -> bool:
        return True

    def __ge__(self, other: "FakeRole") -> bool:
        return self.position >= other.position

    def __le__(self, other: "FakeRole") -> bool:
        return self.position <= other.position


@pytest.mark.asyncio
async def test_create_roles_rejects_privileged_names_and_reports_partial_failures() -> None:
    first_name, second_name = roles.REQUIRED_ROLE_NAMES[:2]
    privileged = FakeRole(first_name, permissions=8)
    top_role = FakeRole("Bot", position=10)
    everyone_role = FakeRole("@everyone", position=0)
    bot_member = SimpleNamespace(
        guild_permissions=SimpleNamespace(manage_roles=True),
        top_role=top_role,
    )

    class RoleGuild:
        id = 100
        name = "Test Guild"
        me = bot_member
        roles = [privileged]
        default_role = everyone_role
        created_kwargs: dict[str, dict[str, object]] = {}

        async def create_role(self, *, name: str, **kwargs: object) -> FakeRole:
            self.created_kwargs[name] = kwargs
            if name == second_name:
                response = SimpleNamespace(status=403, reason="Forbidden", headers={})
                raise discord.Forbidden(response, "denied")
            role = FakeRole(name)
            self.roles.append(role)
            return role

    result = await roles.create_roles(RoleGuild())

    assert result.rejected == {
        first_name: "the existing role has privileged permissions"
    }
    assert result.failed == {second_name: "Discord denied role creation"}
    assert len(result.created) == len(roles.REQUIRED_ROLE_NAMES) - 2
    assert RoleGuild.created_kwargs[roles.ACTIVE_LOBBY_ROLE]["mentionable"] is False
    assert RoleGuild.created_kwargs[roles.ACTIVE_LOBBY_ROLE]["permissions"].value == 0
    assert RoleGuild.created_kwargs[roles.WEBSITE_MODERATOR_ROLE]["mentionable"] is False
    assert RoleGuild.created_kwargs[roles.WEBSITE_MODERATOR_ROLE]["permissions"].value == 0
    assert result.safe_roles[roles.ACTIVE_LOBBY_ROLE].name == roles.ACTIVE_LOBBY_ROLE


@pytest.mark.asyncio
async def test_create_roles_makes_existing_active_lobby_role_non_mentionable() -> None:
    active_role = FakeRole(roles.ACTIVE_LOBBY_ROLE, mentionable=True)
    existing = [
        active_role,
        *[
            FakeRole(name)
            for name in roles.REQUIRED_ROLE_NAMES
            if name != roles.ACTIVE_LOBBY_ROLE
        ],
    ]
    top_role = FakeRole("Bot", position=10)
    everyone_role = FakeRole("@everyone", position=0)
    bot_member = SimpleNamespace(
        guild_permissions=SimpleNamespace(manage_roles=True),
        top_role=top_role,
    )

    class RoleGuild:
        id = 100
        name = "Test Guild"
        me = bot_member
        roles = existing
        default_role = everyone_role

        async def create_role(self, **kwargs: object) -> FakeRole:
            raise AssertionError("all roles already exist")

    result = await roles.create_roles(RoleGuild())

    assert result.failed == {}
    assert active_role.mentionable is False
    assert active_role.edits == [
        {
            "mentionable": False,
            "reason": "Battlevive role safety setup",
        }
    ]
    assert result.safe_roles[roles.ACTIVE_LOBBY_ROLE] is active_role


@pytest.mark.asyncio
async def test_create_roles_makes_existing_website_moderator_non_mentionable() -> None:
    moderator_role = FakeRole(roles.WEBSITE_MODERATOR_ROLE, mentionable=True)
    existing = [
        moderator_role,
        *[
            FakeRole(name)
            for name in roles.REQUIRED_ROLE_NAMES
            if name != roles.WEBSITE_MODERATOR_ROLE
        ],
    ]
    top_role = FakeRole("Bot", position=10)
    everyone_role = FakeRole("@everyone", position=0)
    bot_member = SimpleNamespace(
        guild_permissions=SimpleNamespace(manage_roles=True),
        top_role=top_role,
    )

    class RoleGuild:
        id = 100
        name = "Test Guild"
        me = bot_member
        roles = existing
        default_role = everyone_role

        async def create_role(self, **kwargs: object) -> FakeRole:
            raise AssertionError("all roles already exist")

    result = await roles.create_roles(RoleGuild())

    assert result.failed == {}
    assert moderator_role.mentionable is False
    assert result.safe_roles[roles.WEBSITE_MODERATOR_ROLE] is moderator_role


def test_role_sync_refuses_privileged_reserved_role() -> None:
    role = FakeRole(roles.BATTLEVIVE_PLAYER_ROLE, permissions=8)
    guild = SimpleNamespace(
        id=100,
        name="Test Guild",
        roles=[role],
        me=SimpleNamespace(top_role=FakeRole("Bot", position=10)),
    )

    assert roles._safe_assignable_role(
        guild,
        roles.BATTLEVIVE_PLAYER_ROLE,
    ) is None


@pytest.mark.asyncio
async def test_join_reconciliation_queries_only_linked_or_matching_users(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, object]] = []
    links: list[tuple[str, int]] = []

    class FakePool:
        async def fetchrow(self, query: str, member_id: int) -> None:
            calls.append((query, member_id))
            return None

        async def fetch(
            self,
            query: str,
            candidate_names: list[str],
        ) -> list[dict[str, object]]:
            calls.append((query, candidate_names))
            return [
                {
                    "id": "aa339e7a-19d0-4ce5-be96-ab852ad4b6be",
                    "discord_id": None,
                    "discord_username": "playerone",
                    "mmr": None,
                }
            ]

    async def link(user_id: str, discord_id: int) -> bool:
        links.append((user_id, discord_id))
        return True

    monkeypatch.setattr(roles, "get_pool", lambda: FakePool())
    monkeypatch.setattr(roles.db, "set_user_discord_id", link)
    guild = SimpleNamespace(
        id=100,
        name="Test Guild",
        roles=[],
        me=SimpleNamespace(top_role=SimpleNamespace(position=10)),
    )
    member = SimpleNamespace(
        id=1234,
        name="PlayerOne",
        display_name="Player One",
        global_name=None,
        nick=None,
        guild=guild,
        roles=[],
    )

    await roles.reconcile_member_roles(member)

    assert "WHERE users.discord_id = $1" in calls[0][0]
    assert "OR users.discord_id IS NULL" not in calls[0][0]
    assert "lower(btrim(users.discord_username)) = ANY" in calls[1][0]
    assert calls[1][1] == ["player one", "playerone"]
    assert links == [("aa339e7a-19d0-4ce5-be96-ab852ad4b6be", 1234)]

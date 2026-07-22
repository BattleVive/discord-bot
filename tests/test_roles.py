from __future__ import annotations

from types import SimpleNamespace

import pytest

discord = pytest.importorskip("discord")
pytest.importorskip("asyncpg")

from battlevive_bot import roles


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

    async def empty_query(token: str) -> list[object]:
        return []

    async def no_sync(*args: object, **kwargs: object) -> None:
        return None

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

    monkeypatch.setattr(roles, "query_users", empty_query)
    monkeypatch.setattr(roles, "query_season_ratings", empty_query)
    monkeypatch.setattr(roles, "sync_users_to_db", no_sync)
    monkeypatch.setattr(roles, "sync_season_ratings_to_db", no_sync)
    monkeypatch.setattr(roles.db, "set_user_discord_id", link)
    monkeypatch.setattr(roles, "get_pool", lambda: FakePool())

    bot = SimpleNamespace(guilds=[guild])
    token_manager = SimpleNamespace(JWT_token="token")
    await roles.give_rank_roles(bot, token_manager)

    assert links == [("aa339e7a-19d0-4ce5-be96-ab852ad4b6be", 1234)]
    assert member.added == [silver]

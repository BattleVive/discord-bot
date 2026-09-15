"""Tests for safe creation and reconciliation of managed Discord roles."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from battlevive_gateway.roles import reconcile_member_rank
from battlevive_gateway.roles import create_required_roles


@pytest.mark.asyncio
async def test_rank_reconciliation_replaces_only_existing_managed_rank_tiers() -> None:
    """Verify that rank reconciliation replaces only existing managed rank tiers."""
    silver = SimpleNamespace(name="Silver", managed=False, permissions=SimpleNamespace(value=0), position=1)
    gold = SimpleNamespace(name="Gold", managed=False, permissions=SimpleNamespace(value=0), position=1)
    bot_role = SimpleNamespace(position=10)
    member = SimpleNamespace(roles=[silver], guild=SimpleNamespace(roles=[silver, gold], me=SimpleNamespace(top_role=bot_role)))
    member.added, member.removed = [], []

    async def add_roles(*roles: object, **_: object) -> None:
        """Provide add roles behavior for the test scenario."""
        member.added.extend(roles)

    async def remove_roles(*roles: object, **_: object) -> None:
        """Provide remove roles behavior for the test scenario."""
        member.removed.extend(roles)

    member.add_roles, member.remove_roles = add_roles, remove_roles

    changed = await reconcile_member_rank(member, {"mmr": 2000, "rank": "Gold"})

    assert changed is True
    assert member.removed == [silver]
    assert member.added == [gold]


@pytest.mark.asyncio
async def test_create_roles_records_required_roles_with_their_ownership_purpose() -> None:
    """Verify that create roles records required roles with their ownership purpose."""
    created: list[object] = []
    guild = SimpleNamespace(id=7, roles=[], me=SimpleNamespace(
        guild_permissions=SimpleNamespace(manage_roles=True), top_role=SimpleNamespace(position=10)
    ))

    async def create_role(**kwargs: object) -> object:
        """Provide create role behavior for the test scenario."""
        role = SimpleNamespace(id=len(created) + 1, name=kwargs["name"], managed=False,
                               permissions=SimpleNamespace(value=0), position=1)
        created.append(role)
        guild.roles.append(role)
        return role

    guild.create_role = create_role
    made, existing, blocked = await create_required_roles(guild)

    assert [role.name for role in made] == ["Guide Updates", "BATTLEVIVE", "Diamond", "Platinum", "Gold", "Silver", "Bronze"]
    assert existing == []
    assert blocked == []


@pytest.mark.asyncio
async def test_create_roles_reports_a_same_name_role_that_is_unsafe_to_manage() -> None:
    """Verify that create roles reports a same name role that is unsafe to manage."""
    unsafe = SimpleNamespace(
        name="Gold", managed=True, permissions=SimpleNamespace(value=0), position=1,
    )
    guild = SimpleNamespace(id=7, roles=[unsafe], me=SimpleNamespace(
        guild_permissions=SimpleNamespace(manage_roles=True), top_role=SimpleNamespace(position=10),
    ))

    async def create_role(**kwargs: object) -> object:
        """Provide create role behavior for the test scenario."""
        return SimpleNamespace(id=100, name=kwargs["name"], managed=False,
                               permissions=SimpleNamespace(value=0), position=1)

    guild.create_role = create_role
    _, _, blocked = await create_required_roles(guild)

    assert blocked == ["Gold"]

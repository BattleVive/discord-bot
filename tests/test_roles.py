from __future__ import annotations

from types import SimpleNamespace

import pytest

from battlevive_gateway.roles import reconcile_member_rank
from battlevive_gateway.roles import create_required_roles


@pytest.mark.asyncio
async def test_rank_reconciliation_replaces_only_existing_managed_rank_tiers() -> None:
    silver = SimpleNamespace(name="Silver", managed=False, permissions=SimpleNamespace(value=0), position=1)
    gold = SimpleNamespace(name="Gold", managed=False, permissions=SimpleNamespace(value=0), position=1)
    bot_role = SimpleNamespace(position=10)
    member = SimpleNamespace(roles=[silver], guild=SimpleNamespace(roles=[silver, gold], me=SimpleNamespace(top_role=bot_role)))
    member.added, member.removed = [], []

    async def add_roles(*roles: object, **_: object) -> None:
        member.added.extend(roles)

    async def remove_roles(*roles: object, **_: object) -> None:
        member.removed.extend(roles)

    member.add_roles, member.remove_roles = add_roles, remove_roles

    changed = await reconcile_member_rank(member, {"mmr": 2000, "rank": "Gold"})

    assert changed is True
    assert member.removed == [silver]
    assert member.added == [gold]


@pytest.mark.asyncio
async def test_create_roles_records_required_roles_with_their_ownership_purpose() -> None:
    created: list[object] = []
    guild = SimpleNamespace(id=7, roles=[], me=SimpleNamespace(
        guild_permissions=SimpleNamespace(manage_roles=True), top_role=SimpleNamespace(position=10)
    ))

    async def create_role(**kwargs: object) -> object:
        role = SimpleNamespace(id=len(created) + 1, name=kwargs["name"], managed=False,
                               permissions=SimpleNamespace(value=0), position=1)
        created.append(role)
        guild.roles.append(role)
        return role

    guild.create_role = create_role
    claims: list[tuple[object, ...]] = []
    ownership = SimpleNamespace(claim=lambda *args: claims.append(args))

    async def claim(*args: object) -> None:
        claims.append(args)

    ownership.claim = claim
    made, existing = await create_required_roles(guild, ownership)

    assert made == ["Guide Updates", "BATTLEVIVE", "Diamond", "Platinum", "Gold", "Silver", "Bronze"]
    assert existing == []
    assert claims == [
        (7, "guide_updates", "guide updates", 1),
        (7, "rank", "battlevive", 2),
        (7, "rank", "diamond", 3),
        (7, "rank", "platinum", 4),
        (7, "rank", "gold", 5),
        (7, "rank", "silver", 6),
        (7, "rank", "bronze", 7),
    ]

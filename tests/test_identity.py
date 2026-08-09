from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("discord")
pytest.importorskip("asyncpg")

from battlevive_bot import identity


def member() -> SimpleNamespace:
    return SimpleNamespace(
        id=42,
        name="PlayerOne",
        display_name="Player One",
        global_name=None,
        nick=None,
    )


@pytest.mark.asyncio
async def test_active_link_is_used_without_refresh(monkeypatch) -> None:
    async def linked(discord_id: int):
        return {"id": "user-a", "discord_username": "PlayerOne"}

    monkeypatch.setattr(identity.db, "get_active_user_by_discord_id", linked)
    result = await identity.resolve_member_identity(member())
    assert result.status is identity.IdentityStatus.LINKED


@pytest.mark.asyncio
async def test_ambiguous_active_names_link_neither(monkeypatch) -> None:
    async def absent(discord_id: int):
        return None

    async def candidates(names):
        return [
            {"id": "a", "discord_username": "PlayerOne"},
            {"id": "b", "discord_username": "playerone"},
        ]

    monkeypatch.setattr(identity.db, "get_active_user_by_discord_id", absent)
    monkeypatch.setattr(identity.db, "find_active_users_by_names", candidates)
    result = await identity.resolve_member_identity(member())
    assert result.status is identity.IdentityStatus.AMBIGUOUS


@pytest.mark.asyncio
async def test_absence_is_confirmed_only_after_refresh(monkeypatch) -> None:
    calls = 0

    async def absent(discord_id: int):
        return None

    async def candidates(names):
        return []

    async def refresh() -> bool:
        nonlocal calls
        calls += 1
        return True

    monkeypatch.setattr(identity.db, "get_active_user_by_discord_id", absent)
    monkeypatch.setattr(identity.db, "find_active_users_by_names", candidates)
    result = await identity.resolve_member_identity(member(), refresh)
    assert result.status is identity.IdentityStatus.ABSENT
    assert calls == 1


@pytest.mark.asyncio
async def test_refresh_failure_is_not_reported_as_absence(monkeypatch) -> None:
    async def absent(discord_id: int):
        return None

    async def candidates(names):
        return []

    async def refresh() -> bool:
        return False

    monkeypatch.setattr(identity.db, "get_active_user_by_discord_id", absent)
    monkeypatch.setattr(identity.db, "find_active_users_by_names", candidates)
    result = await identity.resolve_member_identity(member(), refresh)
    assert result.status is identity.IdentityStatus.REFRESH_FAILED

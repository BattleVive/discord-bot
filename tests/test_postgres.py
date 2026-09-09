"""PostgreSQL integration tests for persistent gateway repositories."""

from __future__ import annotations

import os
from pathlib import Path

import asyncpg
import pytest

from battlevive_gateway.repositories import ConcurrentUpdateError
from battlevive_gateway.repositories import GuildConfigRepository
from battlevive_gateway.repositories import IdentityRepository
from battlevive_gateway.repositories import PublicationRepository
from battlevive_gateway.repositories import RoleRepository
from battlevive_gateway.repositories import RuleRepository


SCHEMA = Path(__file__).resolve().parents[1] / "init-db" / "01_schema.sql"


@pytest.fixture
async def connection() -> asyncpg.Connection:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is required for PostgreSQL integration tests")
    connection = await asyncpg.connect(url)
    await connection.execute(SCHEMA.read_text())
    transaction = connection.transaction()
    await transaction.start()
    try:
        yield connection
    finally:
        await transaction.rollback()
        await connection.close()


@pytest.mark.asyncio
async def test_repositories_persist_rules_roles_publications_and_identity_uniqueness(
    connection: asyncpg.Connection,
) -> None:
    guild = GuildConfigRepository(connection)
    await guild.ensure(9_001, 101)
    assert await guild.update(9_001, 1, {"leaderboard_limit": 25}, updated_by=102) == 2
    with pytest.raises(ConcurrentUpdateError):
        await guild.update(9_001, 1, {"leaderboard_limit": 26}, updated_by=103)

    rules = RuleRepository(connection)
    await rules.set(9_001, "refresh", 700, True)
    assert await rules.allows(9_001, "refresh", 700)
    assert not await rules.allows(9_001, "refresh", 701)

    roles = RoleRepository(connection)
    await roles.claim(9_001, "guide", "notification", 800)
    assert await roles.is_owned(9_001, 800)

    publications = PublicationRepository(connection)
    await publications.upsert(
        9_001, "guide", "guide:4", 700, 900, 901, "fingerprint",
        {"title": "Guide", "message_ids": [900]},
    )
    publication = (await publications.list_for_feature(9_001, "guide"))[0]
    assert publication["thread_id"] == 901
    assert publication["metadata"] == {"title": "Guide", "message_ids": [900]}

    identities = IdentityRepository(connection)
    await identities.bind(11, 12, "manual")
    assert not await identities.bind(13, 12, "manual")

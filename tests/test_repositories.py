"""Unit tests for versioned, guild-scoped repository persistence."""

from __future__ import annotations

from unittest.mock import AsyncMock

import asyncpg
import pytest

from battlevive_gateway.repositories import ConcurrentUpdateError
from battlevive_gateway.repositories import GuildConfigRepository
from battlevive_gateway.repositories import IdentityRepository
from battlevive_gateway.repositories import RoleRepository
from battlevive_gateway.repositories import RuleRepository
from battlevive_gateway.repositories import PublicationRepository


class FakeConnection:
    """Provide a fake connection test double."""
    def __init__(self, result: str = "UPDATE 1") -> None:
        """Initialize the fake connection instance."""
        self.result = result
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    async def execute(self, query: str, *args: object) -> str:
        """Provide execute behavior for the test scenario."""
        self.calls.append((query, args))
        return self.result

    async def fetchval(self, _query: str, *_args: object) -> object:
        """Provide fetchval behavior for the test scenario."""
        return None

    def transaction(self) -> object:
        """Provide transaction behavior for the test scenario."""
        class Transaction:
            """Provide a transaction test double."""
            async def __aenter__(self) -> None:
                """Enter the asynchronous context manager."""
                return None
            async def __aexit__(self, *_: object) -> None:
                """Exit the asynchronous context manager."""
                return None
        return Transaction()


@pytest.mark.asyncio
async def test_configuration_update_is_version_checked_and_atomic() -> None:
    """Verify that configuration update is version checked and atomic."""
    connection = FakeConnection()
    repository = GuildConfigRepository(connection)

    version = await repository.update(42, 3, {"leaderboard_limit": 25}, updated_by=9)

    assert version == 4
    query, args = connection.calls[0]
    assert "version = version + 1" in query
    assert "WHERE guild_id = $1 AND version = $2" in query
    assert args == (42, 3, 25, 9)


@pytest.mark.asyncio
async def test_configuration_update_reports_concurrent_write() -> None:
    """Verify that configuration update reports concurrent write."""
    repository = GuildConfigRepository(FakeConnection("UPDATE 0"))
    with pytest.raises(ConcurrentUpdateError):
        await repository.update(42, 3, {"debug_commands_enabled": True}, updated_by=9)


@pytest.mark.asyncio
async def test_identity_binding_uses_both_unique_columns() -> None:
    """Verify that identity binding uses both unique columns."""
    connection = FakeConnection()
    repository = IdentityRepository(connection)
    await repository.bind(100, 200, "manual")
    query, args = connection.calls[-1]
    assert "ON CONFLICT(member_number)" in query
    assert args == (100, 200, "manual")
    lock_calls = [call for call in connection.calls if "pg_advisory_xact_lock" in call[0]]
    assert [call[1] for call in lock_calls] == [(100,), (200,)]


@pytest.mark.asyncio
async def test_identity_binding_returns_false_when_discord_id_unique_constraint_races() -> None:
    """Verify that identity binding returns false when Discord ID unique constraint races."""
    connection = FakeConnection()
    connection.execute = AsyncMock(side_effect=["SELECT 1", "SELECT 1", asyncpg.UniqueViolationError()])

    assert not await IdentityRepository(connection).bind(100, 200, "manual")


@pytest.mark.asyncio
async def test_command_rule_upsert_and_created_role_ownership_are_scoped_to_guild() -> None:
    """Verify that command rule upsert and created role ownership are scoped to guild."""
    connection = FakeConnection()
    await RuleRepository(connection).set(7, "leaderboard", 8, True)
    await RoleRepository(connection).claim(7, "guide", "updates", 9)
    assert "command_channel_rules" in connection.calls[0][0]
    assert connection.calls[0][1] == (7, "leaderboard", 8, True)
    assert "created_roles" in connection.calls[1][0]
    assert connection.calls[1][1] == (7, "guide", "updates", 9)


@pytest.mark.asyncio
async def test_global_command_rule_applies_when_no_command_specific_rule_exists() -> None:
    """Verify that global command rule applies when no command specific rule exists."""
    class RuleConnection(FakeConnection):
        """Provide a rule connection test double."""
        async def fetchval(self, query: str, *_args: object) -> object:
            """Provide fetchval behavior for the test scenario."""
            if "command_name=$2" in query:
                return None
            if "command_name='*'" in query:
                return False
            raise AssertionError(query)

    assert not await RuleRepository(RuleConnection()).allows(7, "rank", 8)


@pytest.mark.asyncio
async def test_publication_reads_include_fingerprint_for_idempotent_reconciliation() -> None:
    """Verify that publication reads include fingerprint for idempotent reconciliation."""
    connection = FakeConnection()
    connection.fetch = __import__("unittest.mock").mock.AsyncMock(return_value=[])

    await PublicationRepository(connection).list_for_feature(7, "leaderboard")

    assert "fingerprint" in connection.fetch.await_args.args[0]


@pytest.mark.asyncio
async def test_publication_reads_decode_json_metadata_for_guide_reconciliation() -> None:
    """Verify that publication reads decode JSON metadata for guide reconciliation."""
    connection = FakeConnection()
    connection.fetch = __import__("unittest.mock").mock.AsyncMock(return_value=[{
        "publication_key": "guide:4", "channel_id": 10, "message_id": None,
        "thread_id": 11, "fingerprint": None,
        "metadata": '{"message_ids":[12,13]}',
    }])

    rows = await PublicationRepository(connection).list_for_feature(7, "guide")

    assert rows[0]["metadata"] == {"message_ids": [12, 13]}

"""Tests for schema verification and database bootstrap migrations."""

from __future__ import annotations

import pytest

from battlevive_gateway.database import SchemaError
from battlevive_gateway.database import verify_schema
from battlevive_gateway.migrations import apply_schema


class Connection:
    """Provide a connection test double."""
    def __init__(self, names: list[str]) -> None:
        """Initialize the connection instance."""
        self.names = names

    async def fetch(self, _: str) -> list[dict[str, str]]:
        """Provide fetch behavior for the test scenario."""
        return [{"table_name": name} for name in self.names]


@pytest.mark.asyncio
async def test_schema_verifier_rejects_missing_expected_table() -> None:
    """Verify that schema verifier rejects missing expected table."""
    with pytest.raises(SchemaError, match="discord_publications"):
        await verify_schema(Connection(["guild_config", "command_channel_rules", "created_roles"]))


@pytest.mark.asyncio
async def test_migration_applies_the_idempotent_bootstrap_schema() -> None:
    """Verify that migration applies the idempotent bootstrap schema."""
    statements: list[str] = []

    class MigrationConnection:
        """Provide a migration connection test double."""
        async def execute(self, statement: str) -> None:
            """Provide execute behavior for the test scenario."""
            statements.append(statement)

    await apply_schema(MigrationConnection())

    assert len(statements) == 1
    assert "CREATE TABLE IF NOT EXISTS guild_config" in statements[0]

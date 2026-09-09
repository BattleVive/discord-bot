"""Tests for schema verification and database bootstrap migrations."""

from __future__ import annotations

import pytest

from battlevive_gateway.database import SchemaError
from battlevive_gateway.database import verify_schema
from battlevive_gateway.migrations import apply_schema


class Connection:
    def __init__(self, names: list[str]) -> None:
        self.names = names

    async def fetch(self, _: str) -> list[dict[str, str]]:
        return [{"table_name": name} for name in self.names]


@pytest.mark.asyncio
async def test_schema_verifier_rejects_missing_expected_table() -> None:
    with pytest.raises(SchemaError, match="identity_links"):
        await verify_schema(Connection(["guild_config", "command_channel_rules", "created_roles", "discord_publications"]))


@pytest.mark.asyncio
async def test_migration_applies_the_idempotent_bootstrap_schema() -> None:
    statements: list[str] = []

    class MigrationConnection:
        async def execute(self, statement: str) -> None:
            statements.append(statement)

    await apply_schema(MigrationConnection())

    assert len(statements) == 1
    assert "CREATE TABLE IF NOT EXISTS guild_config" in statements[0]

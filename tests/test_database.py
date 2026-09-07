from __future__ import annotations

import pytest

from battlevive_gateway.database import SchemaError
from battlevive_gateway.database import verify_schema


class Connection:
    def __init__(self, names: list[str]) -> None:
        self.names = names

    async def fetch(self, _: str) -> list[dict[str, str]]:
        return [{"table_name": name} for name in self.names]


@pytest.mark.asyncio
async def test_schema_verifier_rejects_missing_expected_table() -> None:
    with pytest.raises(SchemaError, match="identity_links"):
        await verify_schema(Connection(["guild_config", "command_channel_rules", "created_roles", "discord_publications"]))

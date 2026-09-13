"""Gateway database lifecycle: verify the bootstrap schema only."""
from __future__ import annotations

from typing import Any

import asyncpg

EXPECTED_TABLES = frozenset({"guild_config", "command_channel_rules", "created_roles", "discord_publications"})


class SchemaError(RuntimeError):
    """Report schema failures."""
    pass


async def verify_schema(connection: Any) -> None:
    """Verify that the database contains every required table."""
    rows = await connection.fetch(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
    )
    present = {row["table_name"] for row in rows}
    missing = sorted(EXPECTED_TABLES - present)
    if missing:
        raise SchemaError("Missing expected table(s): " + ", ".join(missing))


async def connect_and_verify(dsn: str) -> asyncpg.Pool:
    """Create a connection pool and verify its schema."""
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=5, command_timeout=10)
    try:
        async with pool.acquire() as connection:
            await verify_schema(connection)
    except Exception:
        await pool.close()
        raise
    return pool

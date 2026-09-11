"""Idempotent bootstrap-schema migration used by Compose deployments."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import asyncpg


def _schema_path() -> Path:
    """Return the path to the bundled idempotent schema."""
    installed = Path("/app/init-db/01_schema.sql")
    if installed.is_file():
        return installed
    return Path(__file__).resolve().parents[4] / "init-db" / "01_schema.sql"


async def apply_schema(connection: Any) -> None:
    """Apply the idempotent schema before gateway startup."""
    await connection.execute(_schema_path().read_text())


async def migrate(dsn: str) -> None:
    """Connect once, apply the schema, and release the migration connection."""
    connection = await asyncpg.connect(dsn, command_timeout=30)
    try:
        await apply_schema(connection)
    finally:
        await connection.close()


def main() -> None:
    """Run the migrations command-line entry point."""
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        path = os.environ.get("DATABASE_URL_FILE", "").strip()
        if path:
            dsn = Path(path).read_text().strip()
    if not dsn:
        raise RuntimeError("DATABASE_URL is required for schema migration")
    asyncio.run(migrate(dsn))


if __name__ == "__main__":
    main()

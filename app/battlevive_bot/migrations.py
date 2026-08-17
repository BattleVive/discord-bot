from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
from typing import Any

import asyncpg

from .logs import logger
from .settings import DATABASE_URL


MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "init-db"
MIGRATION_LOCK_ID = 4_240_611_986_603_737_711
_MIGRATION_NAME = re.compile(r"^(?P<version>[0-9]+)_.+\.sql$")


class MigrationError(RuntimeError):
    """Raised when migration history is inconsistent or unsafe."""


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    filename: str
    sql: str
    sha256: str


def discover_migrations(directory: Path | str) -> list[Migration]:
    migrations: list[Migration] = []
    versions: set[int] = set()
    for path in sorted(Path(directory).glob("*.sql")):
        match = _MIGRATION_NAME.fullmatch(path.name)
        if match is None:
            continue
        version = int(match.group("version"))
        if version in versions:
            raise MigrationError(f"Duplicate migration version {version}.")
        versions.add(version)
        sql = path.read_text(encoding="utf-8")
        migrations.append(
            Migration(
                version=version,
                filename=path.name,
                sql=sql,
                sha256=hashlib.sha256(sql.encode("utf-8")).hexdigest(),
            )
        )
    return sorted(migrations, key=lambda item: item.version)


async def run_migrations(connection: Any, directory: Path | str) -> list[str]:
    migrations = discover_migrations(directory)
    await connection.execute("SELECT pg_advisory_lock($1)", MIGRATION_LOCK_ID)
    try:
        await connection.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                version bigint PRIMARY KEY,
                filename text NOT NULL UNIQUE,
                sha256 text NOT NULL,
                applied_at timestamptz NOT NULL DEFAULT now()
            )"""
        )
        rows = await connection.fetch(
            "SELECT version, filename, sha256 FROM schema_migrations ORDER BY version"
        )
        history = {int(row["version"]): row for row in rows}
        available = {migration.version for migration in migrations}
        unknown = sorted(set(history) - available)
        if unknown:
            raise MigrationError(
                "Applied migration versions are missing from the image: "
                + ", ".join(map(str, unknown))
                + "."
            )

        applied: list[str] = []
        for migration in migrations:
            previous = history.get(migration.version)
            if previous is not None:
                if (
                    previous["filename"] != migration.filename
                    or previous["sha256"] != migration.sha256
                ):
                    raise MigrationError(
                        f"Migration {migration.version} checksum or filename changed."
                    )
                continue
            async with connection.transaction():
                await connection.execute(migration.sql)
                await connection.execute(
                    "INSERT INTO schema_migrations(version, filename, sha256) "
                    "VALUES ($1, $2, $3)",
                    migration.version,
                    migration.filename,
                    migration.sha256,
                )
            applied.append(migration.filename)
        return applied
    finally:
        await connection.execute("SELECT pg_advisory_unlock($1)", MIGRATION_LOCK_ID)


async def migrate_database() -> None:
    if not DATABASE_URL:
        raise MigrationError("DATABASE_URL is required for migrations.")
    connection = await asyncpg.connect(DATABASE_URL, command_timeout=60)
    try:
        applied = await run_migrations(connection, MIGRATIONS_DIR)
    finally:
        await connection.close()
    logger.info("Database migrations complete; applied %d migration(s).", len(applied))


def main() -> None:
    asyncio.run(migrate_database())


if __name__ == "__main__":
    main()

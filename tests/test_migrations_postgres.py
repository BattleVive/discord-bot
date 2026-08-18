from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import os
from pathlib import Path
from urllib.parse import urlparse
from urllib.parse import urlsplit
from urllib.parse import urlunsplit
import uuid

import asyncpg
import pytest
import pytest_asyncio

from battlevive_bot.migrations import MigrationError
from battlevive_bot.migrations import MIGRATIONS_DIR
from battlevive_bot.migrations import run_migrations


def _postgres_test_url() -> str:
    dsn = os.environ.get("TEST_DATABASE_URL", "")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is not set; skipping PostgreSQL migration tests")
    parsed = urlparse(dsn)
    if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        pytest.skip("TEST_DATABASE_URL must point at localhost")
    if "test" not in parsed.path.lower():
        pytest.skip("TEST_DATABASE_URL database name must contain test")
    return dsn


@pytest_asyncio.fixture
async def postgres_migration_database() -> AsyncIterator[str]:
    dsn = _postgres_test_url()
    try:
        connection = await asyncpg.connect(dsn, timeout=2)
    except (OSError, asyncpg.PostgresError):
        if os.environ.get("CI"):
            raise
        pytest.skip("PostgreSQL test database is not reachable")
    database = f"migration_test_{uuid.uuid4().hex}"
    await connection.execute(f'CREATE DATABASE "{database}" TEMPLATE template0')
    await connection.close()
    parsed = urlsplit(dsn)
    isolated_dsn = urlunsplit(parsed._replace(path=f"/{database}"))
    try:
        yield isolated_dsn
    finally:
        cleanup = await asyncpg.connect(dsn, timeout=2)
        try:
            await cleanup.execute(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
        finally:
            await cleanup.close()


@pytest.mark.asyncio
async def test_real_existing_schema_adoption_repeat_and_checksum_guard(
    postgres_migration_database: str,
    tmp_path: Path,
) -> None:
    connection = await asyncpg.connect(postgres_migration_database, timeout=2)
    try:
        migrations = sorted(MIGRATIONS_DIR.glob("*.sql"))
        for migration in migrations:
            await connection.execute(migration.read_text(encoding="utf-8"))

        applied = await run_migrations(connection, MIGRATIONS_DIR)
        assert applied == [path.name for path in migrations]
        assert await run_migrations(connection, MIGRATIONS_DIR) == []
        assert await connection.fetchval(
            "SELECT count(*) FROM schema_migrations"
        ) == len(migrations)

        for migration in MIGRATIONS_DIR.glob("*.sql"):
            (tmp_path / migration.name).write_bytes(migration.read_bytes())
        first = sorted(tmp_path.glob("*.sql"))[0]
        first.write_text(first.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with pytest.raises(MigrationError, match="checksum"):
            await run_migrations(connection, tmp_path)
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_real_concurrent_migration_runs_serialize(
    postgres_migration_database: str,
    tmp_path: Path,
) -> None:
    (tmp_path / "01_concurrent.sql").write_text(
        "SELECT pg_sleep(0.25); CREATE TABLE concurrency_probe(id integer);",
        encoding="utf-8",
    )
    first = await asyncpg.connect(postgres_migration_database, timeout=2)
    second = await asyncpg.connect(postgres_migration_database, timeout=2)
    try:
        results = await asyncio.gather(
            run_migrations(first, tmp_path),
            run_migrations(second, tmp_path),
        )
        assert sorted(results, key=len) == [[], ["01_concurrent.sql"]]
        assert await first.fetchval("SELECT count(*) FROM schema_migrations") == 1
    finally:
        await first.close()
        await second.close()


@pytest.mark.asyncio
async def test_real_failed_migration_rolls_back_schema_and_ledger(
    postgres_migration_database: str,
    tmp_path: Path,
) -> None:
    (tmp_path / "01_broken.sql").write_text(
        "CREATE TABLE failed_table(id integer); SELECT 1 / 0;",
        encoding="utf-8",
    )
    connection = await asyncpg.connect(postgres_migration_database, timeout=2)
    try:
        with pytest.raises(asyncpg.DivisionByZeroError):
            await run_migrations(connection, tmp_path)
        assert await connection.fetchval("SELECT to_regclass('failed_table')") is None
        assert await connection.fetchval("SELECT count(*) FROM schema_migrations") == 0
    finally:
        await connection.close()

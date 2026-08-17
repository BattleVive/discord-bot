from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from battlevive_bot.migrations import MigrationError
from battlevive_bot.migrations import run_migrations


class FakeTransaction:
    def __init__(self, connection: "FakeConnection") -> None:
        self.connection = connection
        self.snapshot: tuple[list[str], dict[int, tuple[str, str]]] | None = None

    async def __aenter__(self) -> None:
        self.snapshot = (
            self.connection.applied_sql.copy(),
            self.connection.ledger.copy(),
        )

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        if exc is not None and self.snapshot is not None:
            self.connection.applied_sql, self.connection.ledger = self.snapshot


class FakeConnection:
    def __init__(self) -> None:
        self.ledger: dict[int, tuple[str, str]] = {}
        self.applied_sql: list[str] = []
        self.lock_events: list[str] = []

    async def execute(self, sql: str, *args: object) -> str:
        if "pg_advisory_lock" in sql:
            self.lock_events.append("lock")
        elif "pg_advisory_unlock" in sql:
            self.lock_events.append("unlock")
        elif sql.startswith("CREATE TABLE IF NOT EXISTS schema_migrations"):
            pass
        elif sql.startswith("INSERT INTO schema_migrations"):
            version, filename, checksum = args
            self.ledger[int(version)] = (str(filename), str(checksum))
        elif "FAIL MIGRATION" in sql:
            self.applied_sql.append(sql)
            raise RuntimeError("migration failed")
        else:
            self.applied_sql.append(sql)
        return "OK"

    async def fetch(self, sql: str) -> list[dict[str, object]]:
        return [
            {"version": version, "filename": filename, "sha256": checksum}
            for version, (filename, checksum) in self.ledger.items()
        ]

    def transaction(self) -> FakeTransaction:
        return FakeTransaction(self)


@pytest.mark.asyncio
async def test_migrations_apply_in_order_under_advisory_lock(tmp_path: Path) -> None:
    (tmp_path / "02_second.sql").write_text("SELECT 'second';", encoding="utf-8")
    (tmp_path / "01_first.sql").write_text("SELECT 'first';", encoding="utf-8")
    connection = FakeConnection()

    applied = await run_migrations(connection, tmp_path)

    assert applied == ["01_first.sql", "02_second.sql"]
    assert connection.applied_sql == ["SELECT 'first';", "SELECT 'second';"]
    assert connection.lock_events == ["lock", "unlock"]
    assert sorted(connection.ledger) == [1, 2]


@pytest.mark.asyncio
async def test_repeat_migration_run_is_noop(tmp_path: Path) -> None:
    path = tmp_path / "01_initial.sql"
    path.write_text("SELECT 1;", encoding="utf-8")
    connection = FakeConnection()
    await run_migrations(connection, tmp_path)

    assert await run_migrations(connection, tmp_path) == []
    assert connection.applied_sql == ["SELECT 1;"]


@pytest.mark.asyncio
async def test_changed_historical_migration_checksum_fails(tmp_path: Path) -> None:
    path = tmp_path / "01_initial.sql"
    path.write_text("SELECT 2;", encoding="utf-8")
    connection = FakeConnection()
    connection.ledger[1] = (
        "01_initial.sql",
        hashlib.sha256(b"SELECT 1;").hexdigest(),
    )

    with pytest.raises(MigrationError, match="checksum"):
        await run_migrations(connection, tmp_path)

    assert connection.applied_sql == []
    assert connection.lock_events == ["lock", "unlock"]


@pytest.mark.asyncio
async def test_failed_migration_is_transactionally_rolled_back(tmp_path: Path) -> None:
    (tmp_path / "01_broken.sql").write_text(
        "CREATE TABLE example(id int); FAIL MIGRATION",
        encoding="utf-8",
    )
    connection = FakeConnection()

    with pytest.raises(RuntimeError, match="migration failed"):
        await run_migrations(connection, tmp_path)

    assert connection.applied_sql == []
    assert connection.ledger == {}
    assert connection.lock_events == ["lock", "unlock"]

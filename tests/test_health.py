from __future__ import annotations

from datetime import datetime
from datetime import timedelta
from datetime import timezone
import json
from pathlib import Path

import pytest

from battlevive_bot.health import HealthState
from battlevive_bot.health import check_health_file
from battlevive_bot.health import database_is_ready
from battlevive_bot.health import required_services_ready


def test_health_state_atomically_writes_only_non_secret_component_state(
    tmp_path: Path,
) -> None:
    path = tmp_path / "health.json"
    state = HealthState(path)
    now = datetime(2026, 8, 17, 12, 30, tzinfo=timezone.utc)

    state.write(
        discord_ready=True,
        database_ready=True,
        services_ready=True,
        token_persistence_ready=True,
        now=now,
    )

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "status": "healthy",
        "discord_ready": True,
        "database_ready": True,
        "services_ready": True,
        "token_persistence_ready": True,
        "timestamp": "2026-08-17T12:30:00Z",
    }
    assert list(tmp_path.glob(".*.tmp")) == []


def test_any_failed_component_writes_degraded_health(tmp_path: Path) -> None:
    path = tmp_path / "health.json"

    HealthState(path).write(
        discord_ready=True,
        database_ready=False,
        services_ready=True,
        token_persistence_ready=True,
    )

    assert json.loads(path.read_text(encoding="utf-8"))["status"] == "degraded"


def test_health_check_rejects_stale_or_malformed_state(tmp_path: Path) -> None:
    path = tmp_path / "health.json"
    now = datetime(2026, 8, 17, 12, 30, tzinfo=timezone.utc)
    state = HealthState(path)
    state.write(
        discord_ready=True,
        database_ready=True,
        services_ready=True,
        token_persistence_ready=True,
        now=now - timedelta(seconds=91),
    )

    assert check_health_file(path, now=now) is False
    path.write_text("not-json", encoding="utf-8")
    assert check_health_file(path, now=now) is False


def test_health_check_accepts_healthy_90_second_heartbeat(tmp_path: Path) -> None:
    path = tmp_path / "health.json"
    now = datetime(2026, 8, 17, 12, 30, tzinfo=timezone.utc)
    HealthState(path).write(
        discord_ready=True,
        database_ready=True,
        services_ready=True,
        token_persistence_ready=True,
        now=now - timedelta(seconds=90),
    )

    assert check_health_file(path, now=now) is True


@pytest.mark.asyncio
async def test_database_health_requires_select_one_result() -> None:
    class Pool:
        async def fetchval(self, query: str) -> int:
            assert query == "SELECT 1"
            return 1

    assert await database_is_ready(Pool()) is True


@pytest.mark.asyncio
async def test_database_health_converts_connection_failure_to_false() -> None:
    class Pool:
        async def fetchval(self, query: str) -> int:
            raise RuntimeError("database unavailable")

    assert await database_is_ready(Pool()) is False


def test_health_degrades_when_any_required_service_stops() -> None:
    assert required_services_ready([True, True, True, True, True]) is True
    assert required_services_ready([True, True, False, True, True]) is False

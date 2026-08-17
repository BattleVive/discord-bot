from __future__ import annotations

from datetime import datetime
from datetime import timezone
import json
import os
from pathlib import Path
import sys
import tempfile
from collections.abc import Iterable
from typing import Any


HEALTH_PATH = Path("/tmp/battlevive-health.json")
MAX_HEARTBEAT_AGE_SECONDS = 90
_COMPONENTS = (
    "discord_ready",
    "database_ready",
    "services_ready",
    "token_persistence_ready",
)


class HealthState:
    def __init__(self, path: Path | str = HEALTH_PATH) -> None:
        self.path = Path(path)

    def write(
        self,
        *,
        discord_ready: bool,
        database_ready: bool,
        services_ready: bool,
        token_persistence_ready: bool,
        now: datetime | None = None,
    ) -> None:
        timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        components = {
            "discord_ready": bool(discord_ready),
            "database_ready": bool(database_ready),
            "services_ready": bool(services_ready),
            "token_persistence_ready": bool(token_persistence_ready),
        }
        record: dict[str, bool | str] = {
            "status": "healthy" if all(components.values()) else "degraded",
            **components,
            "timestamp": timestamp.isoformat(timespec="seconds").replace(
                "+00:00", "Z"
            ),
        }
        descriptor, temporary_name = tempfile.mkstemp(
            dir=self.path.parent,
            prefix=f".{self.path.name}.",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(record, output, separators=(",", ":"))
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary_path, self.path)
        except BaseException:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
            raise


async def database_is_ready(pool: Any) -> bool:
    try:
        return await pool.fetchval("SELECT 1") == 1
    except Exception:
        return False


def required_services_ready(statuses: Iterable[bool]) -> bool:
    values = tuple(statuses)
    return bool(values) and all(values)


def check_health_file(
    path: Path | str = HEALTH_PATH,
    *,
    now: datetime | None = None,
) -> bool:
    try:
        record = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(record, dict) or set(record) != {
            "status",
            *_COMPONENTS,
            "timestamp",
        }:
            return False
        if record["status"] != "healthy":
            return False
        if any(record[name] is not True for name in _COMPONENTS):
            return False
        timestamp = datetime.fromisoformat(record["timestamp"].replace("Z", "+00:00"))
        current = now or datetime.now(timezone.utc)
        age = (current.astimezone(timezone.utc) - timestamp).total_seconds()
        return 0 <= age <= MAX_HEARTBEAT_AGE_SECONDS
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return False


def main() -> int:
    return 0 if check_health_file() else 1


if __name__ == "__main__":
    sys.exit(main())

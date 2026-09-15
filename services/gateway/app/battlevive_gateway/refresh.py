"""Accurate per-integration refresh reporting."""
from __future__ import annotations

from collections.abc import Awaitable
from collections.abc import Callable
from dataclasses import dataclass

from .integrations import IntegrationUnavailable

Operation = Callable[[], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class RefreshReport:
    """Summarize completed, unavailable, and failed refresh operations."""
    completed: tuple[str, ...]
    unavailable: tuple[str, ...]
    failed: tuple[str, ...]

    def message(self) -> str:
        """Format the refresh summary for a Discord response."""
        parts = []
        if self.completed: parts.append("Completed: " + ", ".join(self.completed))
        if self.unavailable: parts.append("Unavailable: " + ", ".join(self.unavailable))
        if self.failed: parts.append("Failed: " + ", ".join(self.failed))
        return "; ".join(parts) or "No integrations configured."


class RefreshCoordinator:
    """Coordinate refresh operations."""
    def __init__(self, operations: dict[str, Operation]) -> None:
        """Initialize the refresh coordinator instance."""
        self._operations = operations

    async def run(self) -> RefreshReport:
        """Run each refresh operation and classify its outcome."""
        completed: list[str] = []
        unavailable: list[str] = []
        failed: list[str] = []
        for name, operation in self._operations.items():
            try:
                await operation()
            except (ConnectionError, TimeoutError, IntegrationUnavailable):
                unavailable.append(name)
            except Exception:
                failed.append(name)
            else:
                completed.append(name)
        return RefreshReport(tuple(completed), tuple(unavailable), tuple(failed))

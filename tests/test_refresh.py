"""Tests for coordinated manual refresh reporting and failure isolation."""

from __future__ import annotations

import pytest

from battlevive_gateway.refresh import RefreshCoordinator
from battlevive_gateway.integrations import IntegrationUnavailable


@pytest.mark.asyncio
async def test_refresh_reports_completed_failed_and_unavailable_integrations() -> None:
    """Verify that refresh reports completed failed and unavailable integrations."""
    async def completed() -> None:
        """Provide completed behavior for the test scenario."""
        return None
    async def unavailable() -> None:
        """Provide unavailable behavior for the test scenario."""
        raise ConnectionError()
    async def failed() -> None:
        """Provide failed behavior for the test scenario."""
        raise ValueError()
    report = await RefreshCoordinator({"guides": completed, "upstream": unavailable, "renderer": failed}).run()
    assert report.completed == ("guides",)
    assert report.unavailable == ("upstream",)
    assert report.failed == ("renderer",)


@pytest.mark.asyncio
async def test_refresh_reports_private_service_unavailability_without_calling_it_a_failure() -> None:
    """Verify that refresh reports private service unavailability without calling it a failure."""
    async def unavailable() -> None:
        """Provide unavailable behavior for the test scenario."""
        raise IntegrationUnavailable("not reachable")
    report = await RefreshCoordinator({"upstream-data": unavailable}).run()
    assert report.completed == ()
    assert report.unavailable == ("upstream-data",)
    assert report.failed == ()

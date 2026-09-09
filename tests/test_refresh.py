"""Tests for coordinated manual refresh reporting and failure isolation."""

from __future__ import annotations

import pytest

from battlevive_gateway.refresh import RefreshCoordinator
from battlevive_gateway.integrations import IntegrationUnavailable


@pytest.mark.asyncio
async def test_refresh_reports_completed_failed_and_unavailable_integrations() -> None:
    async def completed() -> None: return None
    async def unavailable() -> None: raise ConnectionError()
    async def failed() -> None: raise ValueError()
    report = await RefreshCoordinator({"guides": completed, "upstream": unavailable, "renderer": failed}).run()
    assert report.completed == ("guides",)
    assert report.unavailable == ("upstream",)
    assert report.failed == ("renderer",)


@pytest.mark.asyncio
async def test_refresh_reports_private_service_unavailability_without_calling_it_a_failure() -> None:
    async def unavailable() -> None: raise IntegrationUnavailable("not reachable")
    report = await RefreshCoordinator({"upstream-data": unavailable}).run()
    assert report.completed == ()
    assert report.unavailable == ("upstream-data",)
    assert report.failed == ()

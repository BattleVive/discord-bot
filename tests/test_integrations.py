"""Tests for gateway clients and internal service-boundary diagnostics."""

from __future__ import annotations

import pytest

from battlevive_gateway.diagnostics import upstream_snapshot
from battlevive_gateway.integrations import IntegrationUnavailable
from battlevive_gateway.integrations import ImageRendererClient
from battlevive_gateway.integrations import UpstreamDataClient


@pytest.mark.asyncio
async def test_gateway_translates_internal_transport_failure_to_unavailable() -> None:
    """Verify that gateway translates internal transport failure to unavailable."""
    async def request(_: str) -> object:
        """Provide request behavior for the test scenario."""
        raise TimeoutError()
    client = UpstreamDataClient("http://upstream-data:8081", request=request)
    with pytest.raises(IntegrationUnavailable):
        await client.get("/queue")


@pytest.mark.asyncio
async def test_gateway_client_preserves_freshness_and_requests_fresh_data_for_mutations() -> None:
    """Verify that gateway client preserves freshness and requests fresh data for mutations."""
    paths: list[str] = []

    async def request(path: str) -> object:
        """Provide request behavior for the test scenario."""
        paths.append(path)
        return {"data": {"count": 3}, "freshness": "fresh", "age_seconds": 0}

    client = UpstreamDataClient("http://upstream", request=request)
    response = await client.get_result("/queue", require_fresh=True)

    assert response.data == {"count": 3}
    assert response.age_seconds == 0
    assert paths == ["/queue?fresh=1"]


@pytest.mark.asyncio
async def test_renderer_client_returns_png_bytes_without_a_shared_volume() -> None:
    """Verify that renderer client returns PNG bytes without a shared volume."""
    calls: list[tuple[str, dict[str, object]]] = []

    async def request(path: str, model: dict[str, object]) -> tuple[str, bytes]:
        """Provide request behavior for the test scenario."""
        calls.append((path, model))
        return "image/png", b"\x89PNG\r\n\x1a\n"

    client = ImageRendererClient("http://renderer", render_request=request)
    assert await client.render_leaderboard({"entries": []}) == b"\x89PNG\r\n\x1a\n"
    assert calls == [("/render/leaderboard", {"entries": []})]


@pytest.mark.asyncio
async def test_upstream_diagnostics_redact_unexpected_sensitive_failures() -> None:
    """Verify that upstream diagnostics redact unexpected sensitive failures."""
    class FailingUpstream:
        """Provide a failing upstream test double."""
        async def get_result(self, _: str, *, require_fresh: bool) -> object:
            """Provide get result behavior for the test scenario."""
            assert require_fresh is True
            raise IntegrationUnavailable("Authorization: Bearer secret-value")

    snapshot = await upstream_snapshot(FailingUpstream())

    serialized = str(snapshot)
    assert "secret-value" not in serialized
    assert "Authorization" not in serialized


@pytest.mark.asyncio
async def test_upstream_diagnostics_include_each_catalogued_guide() -> None:
    """Verify that upstream diagnostics include each catalogued guide."""
    calls: list[str] = []

    class Result:
        """Provide a result test double."""
        freshness = "fresh"
        age_seconds = 0

        def __init__(self, data: dict[str, object]) -> None:
            """Initialize the result instance."""
            self.data = data

    class Upstream:
        """Provide a upstream test double."""
        async def get_result(self, path: str, *, require_fresh: bool) -> Result:
            """Provide get result behavior for the test scenario."""
            assert require_fresh is True
            calls.append(path)
            if path == "/guides":
                return Result({"guides": [{"number": 4, "title": "Guide"}]})
            if path == "/leaderboard":
                return Result({"leaderboard": [{"member_number": 42, "player": "Player"}]})
            return Result({"path": path})

    snapshot = await upstream_snapshot(Upstream())

    assert snapshot["routes"]["/guides/4"]["data"] == {"path": "/guides/4"}  # type: ignore[index]
    assert snapshot["routes"]["/guides/4/markdown"]["data"] == {"path": "/guides/4/markdown"}  # type: ignore[index]
    assert snapshot["routes"]["/players/42"]["data"] == {"path": "/players/42"}  # type: ignore[index]
    assert calls == [
        "/queue", "/stats", "/guides", "/guides/4", "/guides/4/markdown",
        "/leaderboard", "/players/42", "/active-matches", "/disputed-matches",
    ]


@pytest.mark.asyncio
async def test_upstream_diagnostics_caps_detail_expansion_and_reports_omitted_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that upstream diagnostics caps detail expansion and reports omitted records."""
    import battlevive_gateway.diagnostics as diagnostics

    monkeypatch.setattr(diagnostics, "_MAX_GUIDE_EXPANSIONS", 1)
    monkeypatch.setattr(diagnostics, "_MAX_PLAYER_EXPANSIONS", 1)
    calls: list[str] = []

    class Result:
        """Provide a result test double."""
        freshness = "fresh"
        age_seconds = 0

        def __init__(self, data: dict[str, object]) -> None:
            """Initialize the result instance."""
            self.data = data

    class Upstream:
        """Provide a upstream test double."""
        async def get_result(self, path: str, *, require_fresh: bool) -> Result:
            """Provide get result behavior for the test scenario."""
            calls.append(path)
            if path == "/guides":
                return Result({"guides": [{"number": 1}, {"number": 2}]})
            if path == "/leaderboard":
                return Result({"leaderboard": [{"member_number": 3}, {"member_number": 4}]})
            return Result({"path": path})

    snapshot = await diagnostics.upstream_snapshot(Upstream())

    assert "/guides/1" in calls and "/guides/2" not in calls
    assert "/players/3" in calls and "/players/4" not in calls
    assert snapshot["truncated"] == {"guides": 1, "players": 1}

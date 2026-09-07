from __future__ import annotations

import pytest

from battlevive_gateway.diagnostics import upstream_snapshot
from battlevive_gateway.integrations import IntegrationUnavailable
from battlevive_gateway.integrations import ImageRendererClient
from battlevive_gateway.integrations import UpstreamDataClient


@pytest.mark.asyncio
async def test_gateway_translates_internal_transport_failure_to_unavailable() -> None:
    async def request(_: str) -> object:
        raise TimeoutError()
    client = UpstreamDataClient("http://upstream-data:8081", request=request)
    with pytest.raises(IntegrationUnavailable):
        await client.get("/queue")


@pytest.mark.asyncio
async def test_gateway_client_preserves_freshness_and_requests_fresh_data_for_mutations() -> None:
    paths: list[str] = []

    async def request(path: str) -> object:
        paths.append(path)
        return {"data": {"count": 3}, "freshness": "fresh", "age_seconds": 0}

    client = UpstreamDataClient("http://upstream", request=request)
    response = await client.get_result("/queue", require_fresh=True)

    assert response.data == {"count": 3}
    assert response.age_seconds == 0
    assert paths == ["/queue?fresh=1"]


@pytest.mark.asyncio
async def test_renderer_client_returns_png_bytes_without_a_shared_volume() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    async def request(path: str, model: dict[str, object]) -> tuple[str, bytes]:
        calls.append((path, model))
        return "image/png", b"\x89PNG\r\n\x1a\n"

    client = ImageRendererClient("http://renderer", render_request=request)
    assert await client.render_leaderboard({"entries": []}) == b"\x89PNG\r\n\x1a\n"
    assert calls == [("/render/leaderboard", {"entries": []})]


@pytest.mark.asyncio
async def test_upstream_diagnostics_redact_unexpected_sensitive_failures() -> None:
    class FailingUpstream:
        async def get_result(self, _: str, *, require_fresh: bool) -> object:
            assert require_fresh is True
            raise IntegrationUnavailable("Authorization: Bearer secret-value")

    snapshot = await upstream_snapshot(FailingUpstream())

    serialized = str(snapshot)
    assert "secret-value" not in serialized
    assert "Authorization" not in serialized


@pytest.mark.asyncio
async def test_upstream_diagnostics_include_each_catalogued_guide() -> None:
    calls: list[str] = []

    class Result:
        freshness = "fresh"
        age_seconds = 0

        def __init__(self, data: dict[str, object]) -> None:
            self.data = data

    class Upstream:
        async def get_result(self, path: str, *, require_fresh: bool) -> Result:
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
        "/leaderboard", "/players/42", "/active-matches", "/recent-matches",
    ]


@pytest.mark.asyncio
async def test_upstream_diagnostics_caps_detail_expansion_and_reports_omitted_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import battlevive_gateway.diagnostics as diagnostics

    monkeypatch.setattr(diagnostics, "_MAX_GUIDE_EXPANSIONS", 1)
    monkeypatch.setattr(diagnostics, "_MAX_PLAYER_EXPANSIONS", 1)
    calls: list[str] = []

    class Result:
        freshness = "fresh"
        age_seconds = 0

        def __init__(self, data: dict[str, object]) -> None:
            self.data = data

    class Upstream:
        async def get_result(self, path: str, *, require_fresh: bool) -> Result:
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

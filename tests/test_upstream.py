from __future__ import annotations

import asyncio
import logging

import pytest

from battlevive_upstream.client import ApiError
from battlevive_upstream.client import BattleViveClient
from battlevive_upstream.client import Freshness
from battlevive_upstream.client import UpstreamResponse
from battlevive_upstream.service import create_app
from battlevive_upstream.service import route_table


@pytest.mark.asyncio
async def test_feature_request_uses_bearer_and_exact_user_agent_without_preflight() -> None:
    requests: list[tuple[str, dict[str, str]]] = []

    async def send(path: str, headers: dict[str, str]) -> UpstreamResponse:
        requests.append((path, headers))
        return UpstreamResponse(200, {"ok": True, "total": 3, "color": "green", "q1": 1, "q2": 1, "q3": 1}, {})

    client = BattleViveClient("https://example.test", "secret-value", send=send)

    queue = await client.queue()
    assert queue["count"] == 3
    assert queue["color"] == "green"
    assert requests == [
        (
            "/api/bot/queue",
            {
                "Authorization": "Bearer secret-value",
                "User-Agent": "BattleViveBot/1.0 (+https://battlevive.com/)",
            },
        )
    ]


def test_client_rejects_a_cleartext_upstream_url_before_any_request() -> None:
    async def send(_: str, __: dict[str, str]) -> UpstreamResponse:
        raise AssertionError("cleartext upstream request must not be attempted")

    with pytest.raises(ValueError, match="HTTPS"):
        BattleViveClient("http://example.test", "secret-value", send=send)


def test_service_rejects_a_cleartext_environment_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BATTLEVIVE_API_BASE_URL", "http://example.test")

    with pytest.raises(ValueError, match="HTTPS"):
        create_app()


@pytest.mark.asyncio
async def test_guide_markdown_uses_the_service_user_agent() -> None:
    requests: list[tuple[str, dict[str, str]]] = []

    async def send(path: str, headers: dict[str, str]) -> UpstreamResponse:
        requests.append((path, headers))
        return UpstreamResponse(200, "# Guide", {})

    client = BattleViveClient("https://example.test", "secret", send=send)
    assert (await client.guide_markdown(4)).data == {"markdown": "# Guide"}
    assert requests[0][0] == "/api/bot/guides/4/markdown"
    assert requests[0][1]["User-Agent"] == "BattleViveBot/1.0 (+https://battlevive.com/)"


@pytest.mark.asyncio
async def test_identical_requests_are_coalesced_and_lru_hits_are_fresh() -> None:
    gate = asyncio.Event()
    calls = 0

    async def send(path: str, headers: dict[str, str]) -> UpstreamResponse:
        nonlocal calls
        calls += 1
        await gate.wait()
        return UpstreamResponse(200, {"ok": True, "total": 1, "color": "yellow", "q1": 0, "q2": 0, "q3": 1}, {"Cache-Control": "max-age=60"})

    client = BattleViveClient("https://example.test", "secret", send=send)
    one = asyncio.create_task(client.queue_result())
    two = asyncio.create_task(client.queue_result())
    await asyncio.sleep(0)
    gate.set()
    assert (await one).freshness is Freshness.FRESH
    assert (await two).freshness is Freshness.FRESH
    assert calls == 1
    assert (await client.queue_result()).freshness is Freshness.FRESH
    assert calls == 1


@pytest.mark.asyncio
async def test_mutation_refuses_stale_cache_and_errors_redact_credentials() -> None:
    async def send(path: str, headers: dict[str, str]) -> UpstreamResponse:
        raise TimeoutError("Authorization: Bearer secret-value")

    client = BattleViveClient("https://example.test", "secret-value", send=send, retries=0)
    with pytest.raises(ApiError) as error:
        await client.queue_result(require_fresh=True)
    assert "secret-value" not in str(error.value)
    assert "Authorization" not in str(error.value)


@pytest.mark.asyncio
async def test_retries_429_with_retry_after_then_returns_normalized_stats() -> None:
    attempts = 0
    sleeps: list[float] = []

    async def send(path: str, headers: dict[str, str]) -> UpstreamResponse:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return UpstreamResponse(429, {}, {"Retry-After": "0"})
        return UpstreamResponse(200, {"ok": True, "registeredPlayers": "12", "matchesPlayed": 4, "guides": 2, "tournaments": 1}, {})

    client = BattleViveClient("https://example.test", "secret", send=send, sleep=sleeps.append)
    response = await client.stats()

    assert attempts == 2
    assert sleeps == [0.0]
    assert response.data["registeredPlayers"] == 12
    assert response.data["matchesPlayed"] == 4


@pytest.mark.asyncio
async def test_schema_drift_is_rejected_without_leaking_response_or_credentials() -> None:
    async def send(path: str, headers: dict[str, str]) -> UpstreamResponse:
        return UpstreamResponse(200, {"ok": True, "total": "not-a-number", "color": "green", "secret": "secret"}, {})

    client = BattleViveClient("https://example.test", "secret", send=send, retries=0)
    with pytest.raises(ApiError, match="queue schema") as error:
        await client.queue_result()
    assert "secret" not in str(error.value)


def test_normalizers_reject_malformed_route_models_and_normalize_known_scalar_fields() -> None:
    with pytest.raises(ApiError, match="guides schema"):
        BattleViveClient._normalize("/api/bot/guides", {"ok": True, "guides": "wrong"})
    with pytest.raises(ApiError, match="player schema"):
        BattleViveClient._normalize("/api/bot/players/4", {"ok": True, "player": {"memberNumber": 0}})
    assert BattleViveClient._normalize("/api/bot/players/4", {"ok": True, "player": {"memberNumber": "4", "name": "Vive"}}) == {
        "ok": True,
        "player": {"memberNumber": 4, "member_number": 4, "name": "Vive"},
    }


def test_live_bot_contract_wrappers_normalize_guides_and_leaderboard() -> None:
    guides = BattleViveClient._normalize(
        "/api/bot/guides",
        {"ok": True, "guides": [{"number": "4", "title": "Guide"}]},
    )
    leaderboard = BattleViveClient._normalize(
        "/api/bot/leaderboard",
        {"ok": True, "season": "Current", "leaderboard": [{"memberNumber": "4", "rank": "Gold"}]},
    )

    assert guides["guides"] == [{"number": 4, "title": "Guide"}]
    assert leaderboard["leaderboard"] == [{"memberNumber": 4, "member_number": 4, "rank": "Gold"}]


def test_internal_gateway_exposes_only_fixed_feature_routes_and_probes() -> None:
    paths = {route.path for route in route_table()}
    assert {"/health", "/ready", "/queue", "/stats", "/guides", "/guides/{number}",
            "/guides/{number}/markdown", "/leaderboard", "/players/{number}",
            "/active-matches", "/recent-matches"} <= paths
    assert "/{path}" not in paths


def test_route_specific_cache_defaults_cover_parameterized_feature_paths() -> None:
    assert BattleViveClient._ttl("/api/bot/guides/4/markdown", {}) == 300
    assert BattleViveClient._ttl("/api/bot/players/4", {}) == 60


@pytest.mark.asyncio
async def test_display_read_returns_exact_stale_age_after_transient_failure() -> None:
    now = 0.0
    responses = [UpstreamResponse(200, {"ok": True, "total": 2, "color": "green", "q1": 0, "q2": 0, "q3": 2}, {"Cache-Control": "max-age=1"})]

    async def send(path: str, headers: dict[str, str]) -> UpstreamResponse:
        if responses:
            return responses.pop(0)
        raise TimeoutError()

    client = BattleViveClient("https://example.test", "secret", send=send, retries=0, clock=lambda: now)
    await client.queue_result()
    now = 7.9
    stale = await client.queue_result()
    assert stale.freshness is Freshness.STALE
    assert stale.age_seconds == 7


@pytest.mark.asyncio
async def test_lru_cache_evicts_oldest_entry_after_256_entries() -> None:
    calls: list[str] = []
    clock_value = 0.0

    def clock() -> float:
        nonlocal clock_value
        clock_value += 1.0
        return clock_value

    async def send(path: str, headers: dict[str, str]) -> UpstreamResponse:
        calls.append(path)
        return UpstreamResponse(200, {"ok": True, "player": {"memberNumber": path.rsplit("/", 1)[1], "name": "Vive"}}, {"Cache-Control": "max-age=60"})

    client = BattleViveClient("https://example.test", "secret", send=send, clock=clock)
    for number in range(1, 258):
        await client.player(number)
    await client.player(1)
    assert calls.count("/api/bot/players/1") == 2


@pytest.mark.asyncio
async def test_retryable_5xx_is_retried_but_redirect_is_not() -> None:
    calls = 0

    async def flaky(path: str, headers: dict[str, str]) -> UpstreamResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            return UpstreamResponse(503, {}, {})
        return UpstreamResponse(200, {"ok": True, "total": 1, "color": "green", "q1": 0, "q2": 0, "q3": 1}, {})

    client = BattleViveClient("https://example.test", "secret", send=flaky, sleep=lambda _: None)
    assert (await client.queue_result()).data["count"] == 1
    assert calls == 2

    async def redirect(path: str, headers: dict[str, str]) -> UpstreamResponse:
        return UpstreamResponse(302, {}, {"Location": "https://unsafe.example"})
    with pytest.raises(ApiError, match="HTTP 302"):
        await BattleViveClient("https://example.test", "secret", send=redirect).queue_result()


@pytest.mark.asyncio
async def test_failed_upstream_requests_log_route_and_status_without_credentials(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def rejected(_: str, __: dict[str, str]) -> UpstreamResponse:
        return UpstreamResponse(404, {"credential": "secret-value"}, {})

    caplog.set_level(logging.INFO, logger="battlevive.upstream")
    with pytest.raises(ApiError, match="HTTP 404"):
        await BattleViveClient("https://example.test", "secret-value", send=rejected).queue_result()

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "route=/api/bot/queue" in messages
    assert "status=404" in messages
    assert "secret-value" not in messages
    assert "Authorization" not in messages

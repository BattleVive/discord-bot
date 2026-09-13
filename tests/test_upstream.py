"""Tests for v1 upstream validation, caching, pagination, and service routes."""

from __future__ import annotations

import asyncio

import pytest

from battlevive_upstream.client import ApiError
from battlevive_upstream.client import BattleViveClient
from battlevive_upstream.client import Freshness
from battlevive_upstream.client import UpstreamResponse
from battlevive_upstream.service import route_table


def queue_payload() -> dict[str, object]:
    """Return a complete documented v1 queue success payload."""
    return {
        "data": {
            "queues": [{"queue_type": "3v3", "players_waiting": 3}],
            "total_players_waiting": 3,
            "updated_at": "2026-09-13T12:00:00+00:00",
        },
        "meta": {},
    }


def player(member_number: int, discord_id: str | None = "123") -> dict[str, object]:
    """Return a complete documented v1 player record."""
    return {
        "member_number": member_number,
        "discord_id": discord_id,
        "display_name": f"Player {member_number}",
        "profile_url": f"https://battlevive.com/players/{member_number}",
        "role": "BATTLEVIVE PLAYER",
        "joined_at": "2026-01-01T00:00:00+00:00",
        "season_id": "2026-3",
        "mmr": 1800,
        "rank": "Silver",
        "wins": 4,
        "losses": 2,
        "win_rate": 66.7,
    }


@pytest.mark.asyncio
async def test_queue_uses_v1_envelope_and_preserves_bearer_authentication() -> None:
    """A retired route or missing v1 envelope validation must break this request."""
    requests: list[tuple[str, dict[str, str]]] = []

    async def send(path: str, headers: dict[str, str]) -> UpstreamResponse:
        requests.append((path, headers))
        return UpstreamResponse(200, queue_payload(), {"Cache-Control": "max-age=60"})

    result = await BattleViveClient("https://example.test", "secret", send=send).queue_result()

    assert result.data == {
        "queues": [{"queue_type": "3v3", "players_waiting": 3}],
        "total_players_waiting": 3,
        "updated_at": "2026-09-13T12:00:00+00:00",
    }
    assert result.freshness is Freshness.FRESH
    assert requests == [(
        "/api/v1/queue",
        {"Authorization": "Bearer secret", "User-Agent": "BattleViveBot/1.0 (+https://battlevive.com/)"},
    )]


@pytest.mark.asyncio
async def test_leaderboard_follows_opaque_player_cursor_until_requested_limit() -> None:
    """Dropping a cursor or inventing one must omit or duplicate leaderboard rows."""
    requests: list[str] = []
    pages = [
        {"data": [player(1), player(2)], "meta": {"season_id": "2026-3", "pagination": {"limit": 2, "has_more": True, "next_cursor": "opaque-next"}}},
        {"data": [player(3)], "meta": {"season_id": "2026-3", "pagination": {"limit": 2, "has_more": False, "next_cursor": None}}},
    ]

    async def send(path: str, _: dict[str, str]) -> UpstreamResponse:
        requests.append(path)
        return UpstreamResponse(200, pages.pop(0), {})

    result = await BattleViveClient("https://example.test", "secret", send=send).leaderboard(limit=3)

    assert [record["member_number"] for record in result.data["leaderboard"]] == [1, 2, 3]
    assert result.data["season"] == "2026-3"
    assert requests == [
        "/api/v1/players?sort=mmr&order=desc&limit=3",
        "/api/v1/players?sort=mmr&order=desc&limit=1&cursor=opaque-next",
    ]


@pytest.mark.asyncio
async def test_player_lookup_by_discord_id_returns_no_profile_for_an_empty_v1_collection() -> None:
    """Treating a missing caller profile as another player would render the wrong rank card."""
    async def send(path: str, _: dict[str, str]) -> UpstreamResponse:
        assert path == "/api/v1/players?discord_id=987654321&limit=1"
        return UpstreamResponse(200, {"data": [], "meta": {"pagination": {"limit": 1, "has_more": False, "next_cursor": None}}}, {})

    result = await BattleViveClient("https://example.test", "secret", send=send).player_by_discord_id(987654321)

    assert result.data == {"player": None}


@pytest.mark.asyncio
async def test_player_lookup_by_discord_id_rejects_invalid_snowflakes_before_request() -> None:
    """Accepting zero or boolean values would permit unsafe cross-system identity selection."""
    async def send(_: str, __: dict[str, str]) -> UpstreamResponse:
        raise AssertionError("invalid Discord IDs must not request the API")

    client = BattleViveClient("https://example.test", "secret", send=send)
    with pytest.raises(ValueError, match="Discord ID"):
        await client.player_by_discord_id(0)


@pytest.mark.asyncio
async def test_matches_request_documented_expansions_and_normalize_team_data() -> None:
    """Omitting teams, draft, or map must prevent full active-lobby rendering."""
    match = {
        "match_id": 88, "title": "Friday lobby", "season_id": "2026-3", "type": "seasonal",
        "state": "drafting", "size": 3, "region": "EU", "url": "https://battlevive.com/matches/88",
        "created_at": "2026-09-13T12:00:00+00:00", "updated_at": "2026-09-13T12:00:00+00:00",
        "winner_team": None, "duration_seconds": None, "ended_at": None,
        "selected_map": {"map_id": "blackstone", "map_name": "Blackstone", "variant": "day"},
        "team_one": {"name": "Blue", "players": []}, "team_two": {"name": "Red", "players": []},
        "draft": {"draft_phase": "in_progress", "draft_step": 3, "bans": [], "picks": [], "picks_hidden": False},
    }

    async def send(path: str, _: dict[str, str]) -> UpstreamResponse:
        assert path == "/api/v1/matches?state=active&include=teams,draft,map&limit=50"
        return UpstreamResponse(200, {"data": [match], "meta": {"pagination": {"limit": 50, "has_more": False, "next_cursor": None}}}, {})

    result = await BattleViveClient("https://example.test", "secret", send=send).active_matches()

    assert result.data["matches"][0]["match_id"] == 88
    assert result.data["matches"][0]["team_one"]["name"] == "Blue"


@pytest.mark.asyncio
async def test_guide_catalog_uses_guide_id_for_markdown_and_content_hash_for_change_detection() -> None:
    """Using the public display number for v1 Markdown would fetch a different guide."""
    guide = {
        "guide_id": 1004, "guide_number": 4, "title": "Varesh",
        "author": {"discord_id": "123", "display_name": "Author"}, "champion_id": "varesh",
        "champion_name": "Varesh", "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-09-13T12:00:00+00:00", "url": "https://battlevive.com/battlerite-guides/4",
        "markdown_url": "https://battlevive.com/api/v1/guides/1004/markdown", "content_hash": "sha256:abc",
        "content_updated_at": "2026-09-13T12:00:00+00:00", "excerpt": "Guide excerpt",
    }
    calls: list[str] = []

    async def send(path: str, _: dict[str, str]) -> UpstreamResponse:
        calls.append(path)
        if path == "/api/v1/guides?limit=100":
            return UpstreamResponse(200, {"data": [guide], "meta": {"pagination": {"limit": 100, "has_more": False, "next_cursor": None}}}, {})
        assert path == "/api/v1/guides/1004/markdown"
        return UpstreamResponse(200, "# Varesh", {})

    client = BattleViveClient("https://example.test", "secret", send=send)
    catalog = await client.guides()
    markdown = await client.guide_markdown(1004)

    assert catalog.data == {"guides": [{**guide, "champion": "Varesh", "number": 1004, "fingerprint": "sha256:abc"}]}
    assert markdown.data == {"markdown": "# Varesh"}
    assert calls == ["/api/v1/guides?limit=100", "/api/v1/guides/1004/markdown"]


@pytest.mark.asyncio
async def test_v1_error_envelope_is_safe_and_does_not_retry_a_not_found_response() -> None:
    """Treating v1 not-found as transient would amplify failed requests."""
    calls = 0

    async def send(_: str, __: dict[str, str]) -> UpstreamResponse:
        nonlocal calls
        calls += 1
        return UpstreamResponse(404, {"error": {"code": "player_not_found", "message": "No player exists.", "details": {}}}, {})

    with pytest.raises(ApiError, match="HTTP 404") as error:
        await BattleViveClient("https://example.test", "secret", send=send).player(7)

    assert calls == 1
    assert "No player exists" not in str(error.value)


def test_internal_service_exposes_only_stable_v1_feature_routes() -> None:
    """An unrestricted proxy would expose credentialed upstream paths to the gateway."""
    paths = {route.path for route in route_table()}
    assert {
        "/health", "/ready", "/queue", "/stats", "/guides", "/guides/{guide_id}",
        "/guides/{guide_id}/markdown", "/leaderboard", "/players/{member_number}",
        "/players/by-discord/{discord_id}", "/active-matches", "/disputed-matches", "/seasons",
    } <= paths
    assert "/{path}" not in paths


@pytest.mark.asyncio
async def test_expired_display_cache_returns_stale_result_after_transport_failure() -> None:
    """Removing stale fallback would unnecessarily hide a recent queue snapshot."""
    now = 0.0
    responses = [UpstreamResponse(200, queue_payload(), {"Cache-Control": "max-age=1"})]

    async def send(_: str, __: dict[str, str]) -> UpstreamResponse:
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
async def test_identical_v1_requests_are_coalesced() -> None:
    """Removing coalescing would duplicate simultaneous credentialed API requests."""
    gate = asyncio.Event()
    calls = 0

    async def send(_: str, __: dict[str, str]) -> UpstreamResponse:
        nonlocal calls
        calls += 1
        await gate.wait()
        return UpstreamResponse(200, queue_payload(), {"Cache-Control": "max-age=60"})

    client = BattleViveClient("https://example.test", "secret", send=send)
    first = asyncio.create_task(client.queue_result())
    second = asyncio.create_task(client.queue_result())
    await asyncio.sleep(0)
    gate.set()
    await asyncio.gather(first, second)

    assert calls == 1

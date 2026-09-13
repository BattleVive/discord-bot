"""Resilient, cached client for the authenticated BattleVive v1 API."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
import logging
import random
import re
from time import monotonic
from typing import Any
from urllib.parse import urlencode
from urllib.parse import urlparse


USER_AGENT = "BattleViveBot/1.0 (+https://battlevive.com/)"
logger = logging.getLogger("battlevive.upstream")
_MAX_CACHE_ENTRIES = 256
_CACHE_CONTROL_MAX_AGE = re.compile(r"(?:^|,)\s*max-age=(\d+)", re.I)
_DEFAULT_TTLS = {
    "/api/v1/queue": 10,
    "/api/v1/stats": 60,
    "/api/v1/players": 60,
    "/api/v1/guides": 300,
    "/api/v1/matches": 10,
    "/api/v1/seasons": 300,
}


class ApiError(RuntimeError):
    """A safe, credential-free upstream failure."""


class Freshness(StrEnum):
    """Describe whether an upstream result came from current or stale data."""

    FRESH = "fresh"
    STALE = "stale"


@dataclass(frozen=True, slots=True)
class UpstreamResponse:
    """Carry the status, body, and headers returned by the upstream API."""

    status: int
    body: Any
    headers: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class ApiResult:
    """Carry normalized API data and its cache freshness."""

    data: dict[str, Any]
    freshness: Freshness
    age_seconds: int


@dataclass(slots=True)
class _Cached:
    """Store one cached response and its freshness timestamps."""

    data: dict[str, Any]
    created: float
    expires: float


Send = Callable[[str, dict[str, str]], Awaitable[UpstreamResponse]]
Sleep = Callable[[float], Awaitable[None] | None]


class BattleViveClient:
    """Fixed-route v1 client with one credential-wide request budget."""

    def __init__(self, base_url: str, api_key: str, *, send: Send,
                 retries: int = 3, clock: Callable[[], float] = monotonic,
                 sleep: Sleep = asyncio.sleep) -> None:
        """Initialize the client and reject non-HTTPS upstream origins."""
        parsed = urlparse(base_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("BattleVive upstream URL must use HTTPS")
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._send = send
        self._retries = retries
        self._clock = clock
        self._sleep = sleep
        self._cache: OrderedDict[str, _Cached] = OrderedDict()
        self._inflight: dict[str, asyncio.Task[ApiResult]] = {}
        self._request_times: list[float] = []
        self._lock = asyncio.Lock()

    async def queue(self) -> dict[str, Any]:
        """Fetch current queue data."""
        return (await self.queue_result()).data

    async def queue_result(self, *, require_fresh: bool = False) -> ApiResult:
        """Fetch current queue data with freshness metadata."""
        return await self._get("/api/v1/queue", require_fresh=require_fresh)

    async def stats(self, *, require_fresh: bool = False) -> ApiResult:
        """Fetch public site statistics with freshness metadata."""
        return await self._get("/api/v1/stats", require_fresh=require_fresh)

    async def leaderboard(self, *, limit: int = 100, require_fresh: bool = False) -> ApiResult:
        """Fetch the top requested number of MMR-sorted player records."""
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("leaderboard limit must be between 1 and 100")
        players, season, freshness, age = await self._paged_players(
            {"sort": "mmr", "order": "desc"}, limit, require_fresh=require_fresh
        )
        return ApiResult({"season": season or "Current season", "leaderboard": players}, freshness, age)

    async def guides(self, *, require_fresh: bool = False) -> ApiResult:
        """Fetch every guide catalog page using v1's opaque cursor."""
        records, freshness, age = await self._paged_collection(
            "/api/v1/guides", "guides", 100, require_fresh=require_fresh
        )
        return ApiResult({"guides": records}, freshness, age)

    async def guide(self, guide_id: int, *, require_fresh: bool = False) -> ApiResult:
        """Fetch one guide by the v1 guide identifier."""
        return await self._get(f"/api/v1/guides/{_positive(guide_id, 'guide ID')}", require_fresh=require_fresh)

    async def guide_markdown(self, guide_id: int, *, require_fresh: bool = False) -> ApiResult:
        """Fetch raw Markdown for one v1 guide identifier."""
        return await self._get(
            f"/api/v1/guides/{_positive(guide_id, 'guide ID')}/markdown", require_fresh=require_fresh
        )

    async def player(self, member_number: int, *, require_fresh: bool = False) -> ApiResult:
        """Fetch one player by its BattleVive member number."""
        return await self._get(
            f"/api/v1/players/{_positive(member_number, 'member number')}", require_fresh=require_fresh
        )

    async def player_by_discord_id(self, discord_id: int, *, require_fresh: bool = False) -> ApiResult:
        """Fetch the one player associated with an exact Discord snowflake."""
        snowflake = _positive(discord_id, "Discord ID")
        page = await self._get(
            _query("/api/v1/players", {"discord_id": str(snowflake), "limit": 1}),
            require_fresh=require_fresh,
        )
        records = page.data["players"]
        return ApiResult({"player": records[0] if records else None}, page.freshness, page.age_seconds)

    async def active_matches(self, *, require_fresh: bool = False) -> ApiResult:
        """Fetch active matches with every expansion needed for Discord presentation."""
        return await self._matches("active", require_fresh=require_fresh)

    async def disputed_matches(self, *, require_fresh: bool = False) -> ApiResult:
        """Fetch disputed matches with every expansion needed for Discord presentation."""
        return await self._matches("disputed", require_fresh=require_fresh)

    async def seasons(self, *, require_fresh: bool = False) -> ApiResult:
        """Fetch season records for display labels."""
        return await self._get("/api/v1/seasons", require_fresh=require_fresh)

    async def _matches(self, state: str, *, require_fresh: bool) -> ApiResult:
        """Fetch all match pages for one documented state filter."""
        records, _, freshness, age = await self._pages(
            "/api/v1/matches", {"state": state, "include": "teams,draft,map"}, 50,
            require_fresh=require_fresh,
        )
        return ApiResult({"matches": records}, freshness, age)

    async def _paged_players(self, filters: dict[str, object], limit: int, *, require_fresh: bool) -> tuple[list[dict[str, Any]], str | None, Freshness, int]:
        """Collect player pages and retain the API's season metadata."""
        records, meta, freshness, age = await self._pages("/api/v1/players", filters, limit, require_fresh=require_fresh)
        return records, meta.get("season_id") if isinstance(meta.get("season_id"), str) else None, freshness, age

    async def _paged_collection(self, base_path: str, collection: str, page_limit: int, *, require_fresh: bool) -> tuple[list[dict[str, Any]], Freshness, int]:
        """Collect a fixed-size paginated resource until its cursor is exhausted."""
        records, _, freshness, age = await self._pages(base_path, {}, page_limit, require_fresh=require_fresh)
        if collection == "players":
            return records, freshness, age
        return records, freshness, age

    async def _pages(self, base_path: str, filters: dict[str, object], limit: int, *, require_fresh: bool) -> tuple[list[dict[str, Any]], dict[str, Any], Freshness, int]:
        """Follow opaque cursors without attempting to inspect or construct them."""
        records: list[dict[str, Any]] = []
        cursor: str | None = None
        final_meta: dict[str, Any] = {}
        freshness, age = Freshness.FRESH, 0
        while len(records) < limit:
            query = dict(filters)
            query["limit"] = limit - len(records)
            if cursor is not None:
                query["cursor"] = cursor
            result = await self._get(_query(base_path, query), require_fresh=require_fresh)
            key = _collection_key(base_path)
            page_records = result.data[key]
            records.extend(page_records[:limit - len(records)])
            final_meta = result.data.get("meta", {})
            freshness = Freshness.STALE if result.freshness is Freshness.STALE else freshness
            age = max(age, result.age_seconds)
            pagination = final_meta.get("pagination") if isinstance(final_meta, dict) else None
            if not isinstance(pagination, dict) or pagination.get("has_more") is not True:
                break
            next_cursor = pagination.get("next_cursor")
            if not isinstance(next_cursor, str) or not next_cursor:
                raise ApiError("Upstream pagination response was invalid.")
            cursor = next_cursor
        return records, final_meta, freshness, age

    async def _get(self, path: str, *, require_fresh: bool = False) -> ApiResult:
        """Return fresh data or an eligible stale-cache fallback."""
        now = self._clock()
        cached = self._cache.get(path)
        if cached and cached.expires > now:
            self._cache.move_to_end(path)
            return ApiResult(cached.data, Freshness.FRESH, 0)
        if not require_fresh and cached:
            try:
                return await self._request_coalesced(path)
            except ApiError:
                return ApiResult(cached.data, Freshness.STALE, int(now - cached.created))
        return await self._request_coalesced(path)

    async def _request_coalesced(self, path: str) -> ApiResult:
        """Coalesce simultaneous requests for the exact v1 route."""
        async with self._lock:
            task = self._inflight.get(path)
            if task is None:
                task = asyncio.create_task(self._request(path))
                self._inflight[path] = task
        try:
            return await task
        finally:
            if task.done():
                async with self._lock:
                    if self._inflight.get(path) is task:
                        self._inflight.pop(path, None)

    async def _request(self, path: str) -> ApiResult:
        """Request, validate, cache, and retry one fixed v1 route."""
        for attempt in range(self._retries + 1):
            await self._limit()
            try:
                response = await self._send(path, {"Authorization": f"Bearer {self._api_key}", "User-Agent": USER_AGENT})
                logger.info("upstream response route=%s status=%s", path, response.status)
                if 200 <= response.status < 300:
                    data = self._normalize(path, response.body)
                    now = self._clock()
                    ttl = self._ttl(path, response.headers)
                    self._cache[path] = _Cached(data, now, now + ttl)
                    self._cache.move_to_end(path)
                    while len(self._cache) > _MAX_CACHE_ENTRIES:
                        self._cache.popitem(last=False)
                    return ApiResult(data, Freshness.FRESH, 0)
                retryable = response.status == 429 or 500 <= response.status < 600
                if response.status in {401, 403}:
                    raise ApiError("Upstream authentication was rejected.")
                if not retryable:
                    raise ApiError(f"Upstream returned HTTP {response.status}.")
                delay = self._retry_delay(attempt, response.headers)
            except ApiError as error:
                logger.warning("upstream request rejected route=%s reason=%s", path, error)
                raise
            except (TimeoutError, asyncio.TimeoutError):
                logger.warning("upstream request timed out route=%s", path)
                delay = self._retry_delay(attempt, {})
            except Exception as error:
                logger.warning("upstream request failed route=%s error_type=%s", path, type(error).__name__)
                raise ApiError("Upstream request failed.") from error
            if attempt == self._retries:
                break
            await self._sleep_for(delay)
        raise ApiError("Upstream is temporarily unavailable.")

    async def _limit(self) -> None:
        """Wait for capacity under the credential-wide request limit."""
        async with self._lock:
            now = self._clock()
            self._request_times[:] = [item for item in self._request_times if now - item < 60]
            if len(self._request_times) >= 100:
                delay = 60 - (now - self._request_times[0])
            else:
                self._request_times.append(now)
                return
        await self._sleep_for(max(delay, 0))
        await self._limit()

    async def _sleep_for(self, delay: float) -> None:
        """Invoke the configured synchronous or asynchronous sleep hook."""
        result = self._sleep(delay)
        if result is not None:
            await result

    @staticmethod
    def _ttl(path: str, headers: Mapping[str, str]) -> int:
        """Resolve cache lifetime from headers and v1 route defaults."""
        match = _CACHE_CONTROL_MAX_AGE.search(headers.get("Cache-Control", ""))
        if match:
            return int(match.group(1))
        route = path.partition("?")[0]
        for prefix, ttl in _DEFAULT_TTLS.items():
            if route == prefix or route.startswith(prefix + "/"):
                return ttl
        return 30

    @staticmethod
    def _retry_delay(attempt: int, headers: Mapping[str, str]) -> float:
        """Calculate bounded server-directed or exponential retry delay."""
        try:
            return min(float(headers.get("Retry-After", "")), 10)
        except ValueError:
            return min(2 ** attempt + random.uniform(0, 0.25), 10)

    @staticmethod
    def _normalize(path: str, body: Any) -> dict[str, Any]:
        """Validate a v1 response and translate it to a narrow internal model."""
        route = path.partition("?")[0]
        if route.endswith("/markdown"):
            if not isinstance(body, str) or not body:
                raise ApiError("Upstream response did not match its expected guide Markdown schema.")
            return {"markdown": body}
        data, meta = _envelope(body)
        if route == "/api/v1/queue":
            return _object(data, "queue")
        if route == "/api/v1/stats":
            return _object(data, "stats")
        if route == "/api/v1/players":
            return {"players": _players(data), "meta": meta}
        if route.startswith("/api/v1/players/"):
            return {"player": _player(_object(data, "player"))}
        if route == "/api/v1/guides":
            return {"guides": _guides(data), "meta": meta}
        if route.startswith("/api/v1/guides/"):
            return {"guide": _guide(_object(data, "guide"))}
        if route == "/api/v1/matches":
            return {"matches": _matches(data), "meta": meta}
        if route == "/api/v1/seasons":
            if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
                raise ApiError("Upstream response did not match its expected season collection schema.")
            return {"seasons": [dict(item) for item in data], "meta": meta}
        raise ApiError("Upstream response selected an unsupported route.")


def _positive(value: object, label: str) -> int:
    """Return a positive integer while refusing booleans and numeric strings."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _query(path: str, values: Mapping[str, object]) -> str:
    """Append explicitly supplied query values in deterministic insertion order."""
    return path + "?" + urlencode(values, safe=",") if values else path


def _collection_key(path: str) -> str:
    """Return the normalized collection key associated with one resource path."""
    route = path.partition("?")[0]
    if route == "/api/v1/players":
        return "players"
    if route == "/api/v1/guides":
        return "guides"
    if route == "/api/v1/matches":
        return "matches"
    raise ApiError("Upstream pagination selected an unsupported route.")


def _envelope(body: Any) -> tuple[Any, dict[str, Any]]:
    """Unwrap and validate the documented v1 success envelope."""
    if not isinstance(body, dict) or "data" not in body or not isinstance(body.get("meta"), dict):
        raise ApiError("Upstream response did not match its expected v1 envelope.")
    return body["data"], dict(body["meta"])


def _object(value: Any, label: str) -> dict[str, Any]:
    """Copy a required object payload without accepting subclasses or lists."""
    if not isinstance(value, dict):
        raise ApiError(f"Upstream response did not match its expected {label} schema.")
    return dict(value)


def _players(value: Any) -> list[dict[str, Any]]:
    """Normalize a v1 player collection."""
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ApiError("Upstream response did not match its expected player collection schema.")
    return [_player(item) for item in value]


def _player(value: dict[str, Any]) -> dict[str, Any]:
    """Validate fields required to render a player or decide guild eligibility."""
    player = dict(value)
    player["member_number"] = _positive(player.get("member_number"), "member number")
    discord_id = player.get("discord_id")
    if discord_id is None:
        player["discord_id"] = None
    elif isinstance(discord_id, str) and discord_id.isdigit() and int(discord_id) > 0:
        player["discord_id"] = int(discord_id)
    else:
        raise ApiError("Upstream response did not match its expected Discord ID schema.")
    for field in ("display_name", "rank"):
        if not isinstance(player.get(field), str) or not player[field].strip():
            raise ApiError("Upstream response did not match its expected player schema.")
        player[field] = player[field].strip()
    for field in ("mmr", "wins", "losses"):
        player[field] = _nonnegative(player.get(field), field)
    return player


def _nonnegative(value: Any, label: str) -> int:
    """Return one required non-negative integer without accepting booleans."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ApiError(f"Upstream response did not match its expected {label} schema.")
    return value


def _guides(value: Any) -> list[dict[str, Any]]:
    """Normalize a v1 guide collection."""
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ApiError("Upstream response did not match its expected guide collection schema.")
    return [_guide(item) for item in value]


def _guide(value: dict[str, Any]) -> dict[str, Any]:
    """Add stable internal guide aliases while retaining canonical v1 fields."""
    guide = dict(value)
    guide_id = _positive(guide.get("guide_id"), "guide ID")
    if not isinstance(guide.get("title"), str) or not guide["title"].strip():
        raise ApiError("Upstream response did not match its expected guide schema.")
    guide["title"] = guide["title"].strip()
    champion = guide.get("champion_name")
    guide["champion"] = champion.strip() if isinstance(champion, str) and champion.strip() else None
    guide["number"] = guide_id
    content_hash = guide.get("content_hash")
    guide["fingerprint"] = content_hash if isinstance(content_hash, str) and content_hash else None
    return guide


def _matches(value: Any) -> list[dict[str, Any]]:
    """Normalize v1 match records including requested rich expansions."""
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ApiError("Upstream response did not match its expected match collection schema.")
    matches: list[dict[str, Any]] = []
    for source in value:
        record = dict(source)
        record["match_id"] = _positive(record.get("match_id"), "match ID")
        if not isinstance(record.get("state"), str) or not record["state"].strip():
            raise ApiError("Upstream response did not match its expected match schema.")
        record["size"] = _positive(record.get("size"), "match size")
        for key in ("team_one", "team_two"):
            team = record.get(key)
            if not isinstance(team, dict) or not isinstance(team.get("players"), list):
                raise ApiError("Upstream response did not match its expected match-team schema.")
            record[key] = dict(team)
        if not isinstance(record.get("draft"), dict) or not isinstance(record.get("selected_map"), dict):
            raise ApiError("Upstream response did not include required match expansions.")
        matches.append(record)
    return matches

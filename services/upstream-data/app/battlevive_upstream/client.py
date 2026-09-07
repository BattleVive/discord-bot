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
from urllib.parse import urlparse

USER_AGENT = "BattleViveBot/1.0 (+https://battlevive.com/)"
logger = logging.getLogger("battlevive.upstream")
_MAX_CACHE_ENTRIES = 256
_CACHE_CONTROL_MAX_AGE = re.compile(r"(?:^|,)\s*max-age=(\d+)", re.I)
_DEFAULT_TTLS = {"/api/bot/queue": 10, "/api/bot/stats": 60, "/api/bot/leaderboard": 60,
                 "/api/bot/matches/active": 10, "/api/bot/matches/recent": 30,
                 "/api/bot/guides": 300, "/api/bot/players": 60}


class ApiError(RuntimeError):
    """A safe, credential-free upstream failure."""


class Freshness(StrEnum):
    FRESH = "fresh"
    STALE = "stale"


@dataclass(frozen=True, slots=True)
class UpstreamResponse:
    status: int
    body: Any
    headers: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class ApiResult:
    data: dict[str, Any]
    freshness: Freshness
    age_seconds: int


@dataclass(slots=True)
class _Cached:
    data: dict[str, Any]
    created: float
    expires: float


Send = Callable[[str, dict[str, str]], Awaitable[UpstreamResponse]]
Sleep = Callable[[float], Awaitable[None] | None]


class BattleViveClient:
    """Fixed-route BattleVive client with one credential-wide request budget."""

    def __init__(self, base_url: str, api_key: str, *, send: Send,
                 retries: int = 3, clock: Callable[[], float] = monotonic,
                 sleep: Sleep = asyncio.sleep) -> None:
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
        return (await self.queue_result()).data

    async def queue_result(self, *, require_fresh: bool = False) -> ApiResult:
        return await self._get("/api/bot/queue", require_fresh=require_fresh)

    async def stats(self, *, require_fresh: bool = False) -> ApiResult: return await self._get("/api/bot/stats", require_fresh=require_fresh)
    async def leaderboard(self, *, require_fresh: bool = False) -> ApiResult: return await self._get("/api/bot/leaderboard", require_fresh=require_fresh)
    async def guides(self, *, require_fresh: bool = False) -> ApiResult: return await self._get("/api/bot/guides", require_fresh=require_fresh)
    async def guide(self, number: int, *, require_fresh: bool = False) -> ApiResult: return await self._get(f"/api/bot/guides/{number}", require_fresh=require_fresh)
    async def guide_markdown(self, number: int, *, require_fresh: bool = False) -> ApiResult: return await self._get(f"/api/bot/guides/{number}/markdown", require_fresh=require_fresh)
    async def player(self, number: int, *, require_fresh: bool = False) -> ApiResult: return await self._get(f"/api/bot/players/{number}", require_fresh=require_fresh)
    async def active_matches(self, *, require_fresh: bool = False) -> ApiResult: return await self._get("/api/bot/matches/active", require_fresh=require_fresh)
    async def recent_matches(self, *, require_fresh: bool = False) -> ApiResult: return await self._get("/api/bot/matches/recent", require_fresh=require_fresh)

    async def _get(self, path: str, *, require_fresh: bool = False) -> ApiResult:
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
        result = self._sleep(delay)
        if result is not None:
            await result

    @staticmethod
    def _ttl(path: str, headers: Mapping[str, str]) -> int:
        match = _CACHE_CONTROL_MAX_AGE.search(headers.get("Cache-Control", ""))
        if match:
            return int(match.group(1))
        for route, ttl in _DEFAULT_TTLS.items():
            if path == route or path.startswith(route + "/"):
                return ttl
        return 30

    @staticmethod
    def _retry_delay(attempt: int, headers: Mapping[str, str]) -> float:
        try: return min(float(headers.get("Retry-After", "")), 10)
        except ValueError: return min(2 ** attempt + random.uniform(0, 0.25), 10)

    @staticmethod
    def _normalize(path: str, body: Any) -> dict[str, Any]:
        if path.endswith("/markdown"):
            if not isinstance(body, str) or not body:
                raise ApiError("Upstream response did not match its expected guide markdown schema.")
            return {"markdown": body}
        if not isinstance(body, dict):
            raise ApiError("Upstream response did not match its expected schema.")
        normalized = dict(body)
        if body.get("ok") is not True:
            raise ApiError("Upstream response did not match its expected schema.")
        if path == "/api/bot/queue":
            count = _integer(body.get("total"), minimum=0)
            color = body.get("color")
            if count is None or not isinstance(color, str) or not color:
                raise ApiError("Upstream response did not match its expected queue schema.")
            normalized["count"] = count
        elif path == "/api/bot/stats":
            if not body:
                raise ApiError("Upstream response did not match its expected stats schema.")
            for key in ("registeredPlayers", "matchesPlayed", "guides", "tournaments"):
                if key in body:
                    value = _integer(body[key], minimum=0)
                    if value is None:
                        raise ApiError("Upstream response did not match its expected stats schema.")
                    normalized[key] = value
        elif path == "/api/bot/guides":
            _normalize_collection(normalized, "guides", "guides")
        elif path.startswith("/api/bot/guides/"):
            guide = body.get("guide")
            if not isinstance(guide, dict):
                raise ApiError("Upstream response did not match its expected guide schema.")
            normalized["guide"] = dict(guide)
            _normalize_record(normalized["guide"], "guide")
        elif path == "/api/bot/leaderboard":
            _normalize_collection(normalized, "leaderboard", "leaderboard")
        elif path.startswith("/api/bot/players/"):
            player = body.get("player")
            if not isinstance(player, dict):
                raise ApiError("Upstream response did not match its expected player schema.")
            normalized["player"] = dict(player)
            _normalize_record(normalized["player"], "player", require_member=True)
        elif path == "/api/bot/matches/active":
            _normalize_collection(normalized, "matches", "active matches")
        elif path == "/api/bot/matches/recent":
            _normalize_collection(normalized, "matches", "recent matches")
        return normalized


def _integer(value: Any, *, minimum: int | None = None) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        result = value
    elif isinstance(value, str) and value.strip().isdigit():
        result = int(value.strip())
    else:
        return None
    return result if minimum is None or result >= minimum else None


def _normalize_record(record: dict[str, Any], label: str, *, require_member: bool = False) -> None:
    for key in ("member_number", "memberNumber", "number", "id", "season_rating", "rating", "mmr", "position", "wins", "losses"):
        if key in record:
            value = _integer(record[key], minimum=1 if key in {"member_number", "memberNumber", "number", "id", "position"} else 0)
            if value is None:
                raise ApiError(f"Upstream response did not match its expected {label} schema.")
            record[key] = value
    if "memberNumber" in record:
        record["member_number"] = record["memberNumber"]
    if require_member and _integer(record.get("memberNumber", record.get("member_number")), minimum=1) is None:
        raise ApiError(f"Upstream response did not match its expected {label} schema.")


def _normalize_collection(record: dict[str, Any], key: str, label: str, *, alternate: str | None = None) -> None:
    actual_key = key if key in record else alternate
    values = record.get(actual_key) if actual_key is not None else None
    if not isinstance(values, list) or not all(isinstance(item, dict) for item in values):
        raise ApiError(f"Upstream response did not match its expected {label} schema.")
    clean = [dict(item) for item in values]
    for item in clean:
        _normalize_record(item, label)
    record[key] = clean
    if actual_key != key:
        record.pop(actual_key, None)

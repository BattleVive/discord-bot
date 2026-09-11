"""Private HTTP gateway exposing allowlisted BattleVive API routes."""

from __future__ import annotations

import os
import logging
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlparse

from aiohttp import ClientSession
from aiohttp import ClientTimeout
from aiohttp import web

from .client import ApiError
from .client import BattleViveClient
from .client import UpstreamResponse


def configure_logging() -> None:
    """Configure structured logging for the upstream service."""
    level = getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        force=True,
    )


async def aiohttp_sender(base_url: str, path: str, headers: dict[str, str]) -> UpstreamResponse:
    """Perform one bounded, redirect-free upstream request."""
    timeout = ClientTimeout(total=10)
    async with ClientSession(timeout=timeout) as session:
        async with session.get(base_url.rstrip("/") + path, headers=headers, allow_redirects=False) as response:
            if path.endswith("/markdown"):
                return UpstreamResponse(response.status, await response.text(), dict(response.headers))
            try:
                body: Any = await response.json(content_type=None)
            except Exception as error:
                raise ApiError("Upstream returned invalid JSON.") from error
            return UpstreamResponse(response.status, body, dict(response.headers))


def route_table() -> web.RouteTableDef:
    """Build the upstream service's fixed HTTP route table."""
    routes = web.RouteTableDef()

    async def result(request: web.Request, method: str, *args: int) -> web.Response:
        """Call one allowlisted client method and serialize its result."""
        client: BattleViveClient = request.app["client"]
        try:
            kwargs = {"require_fresh": True} if request.query.get("fresh") == "1" else {}
            response = await getattr(client, method)(*args, **kwargs)
        except ApiError as error:
            logging.getLogger("battlevive.upstream").warning("internal route unavailable route=%s reason=%s", request.path, error)
            return web.json_response({"error": str(error)}, status=503)
        return web.json_response({"data": response.data, "freshness": response.freshness, "age_seconds": response.age_seconds})

    @routes.get("/health")
    async def health(_: web.Request) -> web.Response:
        """Report upstream service process health."""
        return web.json_response({"status": "ok"})

    @routes.get("/ready")
    async def ready(request: web.Request) -> web.Response:
        """Report whether upstream credentials are configured."""
        return web.json_response({"status": "ready" if request.app["ready"] else "not_ready"}, status=200 if request.app["ready"] else 503)

    @routes.get("/queue")
    async def queue(request: web.Request) -> web.Response:
        """Return current queue data."""
        return await result(request, "queue_result")
    @routes.get("/stats")
    async def stats(request: web.Request) -> web.Response:
        """Return current BattleVive statistics."""
        return await result(request, "stats")
    @routes.get("/guides")
    async def guides(request: web.Request) -> web.Response:
        """Return the current guide catalog."""
        return await result(request, "guides")
    @routes.get("/guides/{number}")
    async def guide(request: web.Request) -> web.Response:
        """Return one guide by number."""
        number = _positive_number(request)
        return web.json_response({"error": "guide number must be positive"}, status=400) if number is None else await result(request, "guide", number)
    @routes.get("/guides/{number}/markdown")
    async def markdown(request: web.Request) -> web.Response:
        """Return one guide's Markdown by number."""
        number = _positive_number(request)
        return web.json_response({"error": "guide number must be positive"}, status=400) if number is None else await result(request, "guide_markdown", number)
    @routes.get("/leaderboard")
    async def leaderboard(request: web.Request) -> web.Response:
        """Return the current leaderboard."""
        return await result(request, "leaderboard")
    @routes.get("/players/{number}")
    async def player(request: web.Request) -> web.Response:
        """Return one player by number."""
        number = _positive_number(request)
        return web.json_response({"error": "player number must be positive"}, status=400) if number is None else await result(request, "player", number)
    @routes.get("/active-matches")
    async def active(request: web.Request) -> web.Response:
        """Return current active matches."""
        return await result(request, "active_matches")
    @routes.get("/recent-matches")
    async def recent(request: web.Request) -> web.Response:
        """Return recent matches."""
        return await result(request, "recent_matches")
    return routes


def _positive_number(request: web.Request) -> int | None:
    """Validate and return a positive numeric setting."""
    try:
        number = int(request.match_info["number"])
    except ValueError:
        return None
    return number if number > 0 else None


def create_app(client: BattleViveClient | None = None) -> web.Application:
    """Create the allowlisted upstream-data HTTP application."""
    key = os.environ.get("BATTLEVIVE_API_KEY", "")
    base_url = os.environ.get("BATTLEVIVE_API_BASE_URL", "https://battlevive.com")
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("BATTLEVIVE_API_BASE_URL must use HTTPS")
    if client is None:
        async def send(path: str, headers: dict[str, str]) -> UpstreamResponse:
            """Send one request to the configured BattleVive API."""
            return await aiohttp_sender(base_url, path, headers)
        client = BattleViveClient(base_url, key, send=send)
    app = web.Application(client_max_size=64 * 1024)
    app["client"] = client
    app["ready"] = bool(key)
    app.add_routes(route_table())
    return app


def main() -> None:
    """Run the service command-line entry point."""
    configure_logging()
    web.run_app(create_app(), host="0.0.0.0", port=8081)


if __name__ == "__main__":
    main()

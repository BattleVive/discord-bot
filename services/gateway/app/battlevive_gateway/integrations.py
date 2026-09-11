"""Gateway clients for private services."""
from __future__ import annotations

from collections.abc import Awaitable
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import aiohttp


class IntegrationUnavailable(RuntimeError):
    """An optional private integration could not satisfy this operation."""


Request = Callable[[str], Awaitable[object]]
RenderRequest = Callable[[str, dict[str, object]], Awaitable[tuple[str, bytes]]]


@dataclass(frozen=True, slots=True)
class InternalResult:
    """Carry validated private-service data and its freshness metadata."""
    data: dict[str, Any]
    freshness: str
    age_seconds: int


class UpstreamDataClient:
    """Provide access to the upstream data service."""
    def __init__(self, base_url: str, *, request: Request | None = None) -> None:
        """Initialize the upstream data client instance."""
        self._base_url = base_url.rstrip("/")
        self._request = request or self._http_request

    async def get(self, path: str) -> dict[str, Any]:
        """Fetch data from a fixed upstream service path."""
        return (await self.get_result(path)).data

    async def get_result(self, path: str, *, require_fresh: bool = False) -> InternalResult:
        """Fetch and validate data plus its freshness metadata."""
        if require_fresh:
            separator = "&" if "?" in path else "?"
            path = f"{path}{separator}fresh=1"
        try:
            response = await self._request(path)
        except Exception as error:
            raise IntegrationUnavailable("Upstream data is temporarily unavailable.") from error
        if not isinstance(response, dict) or not isinstance(response.get("data"), dict):
            raise IntegrationUnavailable("Upstream data returned an invalid response.")
        freshness = response.get("freshness")
        age_seconds = response.get("age_seconds")
        if freshness not in {"fresh", "stale"} or isinstance(age_seconds, bool) or not isinstance(age_seconds, int) or age_seconds < 0:
            raise IntegrationUnavailable("Upstream data returned an invalid response.")
        if require_fresh and freshness != "fresh":
            raise IntegrationUnavailable("Upstream data could not establish freshness.")
        return InternalResult(response["data"], freshness, age_seconds)

    async def _http_request(self, path: str) -> object:
        """Perform one bounded HTTP request to the upstream service."""
        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(self._base_url + path) as response:
                if response.status >= 400:
                    raise IntegrationUnavailable("Upstream data is temporarily unavailable.")
                return await response.json()


class ImageRendererClient:
    """Small fixed-purpose client for the isolated renderer."""

    def __init__(self, base_url: str, *, render_request: RenderRequest | None = None) -> None:
        """Initialize the image renderer client instance."""
        self._base_url = base_url.rstrip("/")
        self._render_request = render_request or self._http_render

    async def ready(self) -> None:
        """Require a successful renderer readiness response."""
        timeout = aiohttp.ClientTimeout(total=5)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(self._base_url + "/ready") as response:
                    if response.status != 200:
                        raise IntegrationUnavailable("Image renderer is temporarily unavailable.")
                    payload = await response.json()
        except IntegrationUnavailable:
            raise
        except Exception as error:
            raise IntegrationUnavailable("Image renderer is temporarily unavailable.") from error
        if not isinstance(payload, dict) or payload.get("status") != "ready":
            raise IntegrationUnavailable("Image renderer returned an invalid response.")

    async def render_rank(self, model: dict[str, object]) -> bytes:
        """Render a rank-card model as PNG bytes."""
        return await self._render("/render/rank", model)

    async def render_leaderboard(self, model: dict[str, object]) -> bytes:
        """Render a leaderboard model as PNG bytes."""
        return await self._render("/render/leaderboard", model)

    async def _render(self, path: str, model: dict[str, object]) -> bytes:
        """Render and validate a PNG through a fixed renderer path."""
        try:
            content_type, image = await self._render_request(path, model)
        except Exception as error:
            raise IntegrationUnavailable("Image renderer is temporarily unavailable.") from error
        if content_type != "image/png" or not image.startswith(b"\x89PNG\r\n\x1a\n"):
            raise IntegrationUnavailable("Image renderer returned an invalid image.")
        return image

    async def _http_render(self, path: str, model: dict[str, object]) -> tuple[str, bytes]:
        """Send one bounded render request to the image service."""
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(self._base_url + path, json=model) as response:
                if response.status != 200:
                    raise IntegrationUnavailable("Image renderer is temporarily unavailable.")
                return response.content_type, await response.read()

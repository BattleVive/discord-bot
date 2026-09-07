from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from aiohttp import web

from .renderer import render_leaderboard
from .renderer import render_rank


_RENDER_TIMEOUT_SECONDS = 10
_MAX_CONCURRENT_RENDERS = 2
_render_slots = asyncio.Semaphore(_MAX_CONCURRENT_RENDERS)
Renderer = Callable[[dict[str, Any]], bytes]


class RenderBusy(RuntimeError):
    """All renderer workers are occupied by active CPU-bound requests."""


async def render_model(payload: object, renderer: Renderer) -> bytes:
    """Validate and bound one CPU-heavy renderer invocation."""
    if not isinstance(payload, dict):
        raise ValueError("render model must be an object")
    slots = _render_slots
    if slots.locked():
        raise RenderBusy("renderer is busy")
    await slots.acquire()
    worker = asyncio.create_task(asyncio.to_thread(renderer, payload))

    def release_slot(completed: asyncio.Task[bytes]) -> None:
        try:
            completed.exception()
        except asyncio.CancelledError:
            pass
        slots.release()

    try:
        return await asyncio.wait_for(asyncio.shield(worker), timeout=_RENDER_TIMEOUT_SECONDS)
    finally:
        if worker.done():
            slots.release()
        else:
            worker.add_done_callback(release_slot)


def create_app() -> web.Application:
    app = web.Application(client_max_size=64 * 1024)
    async def health(_: web.Request) -> web.Response: return web.json_response({"status": "ok"})
    async def ready(_: web.Request) -> web.Response: return web.json_response({"status": "ready"})
    async def render(request: web.Request, renderer: Renderer) -> web.Response:
        try:
            payload = await request.json()
            image = await render_model(payload, renderer)
        except asyncio.TimeoutError:
            return web.json_response({"error": "rendering timed out"}, status=503)
        except RenderBusy:
            return web.json_response({"error": "renderer is busy"}, status=503)
        except (ValueError, TypeError):
            return web.json_response({"error": "invalid render model"}, status=400)
        return web.Response(body=image, content_type="image/png")
    async def rank(request: web.Request) -> web.Response: return await render(request, render_rank)
    async def leaderboard(request: web.Request) -> web.Response: return await render(request, render_leaderboard)
    app.router.add_get("/health", health)
    app.router.add_get("/ready", ready)
    app.router.add_post("/render/rank", rank)
    app.router.add_post("/render/leaderboard", leaderboard)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="0.0.0.0", port=8082)

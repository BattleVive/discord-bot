"""Tests for renderer input validation, HTTP responses, and concurrency."""

from __future__ import annotations

import base64
import asyncio
from io import BytesIO
from threading import Event

from PIL import Image

import pytest

from battlevive_renderer.renderer import render_leaderboard
from battlevive_renderer.renderer_service import render_model
from battlevive_renderer.renderer_service import RenderBusy
from battlevive_renderer.renderer import render_rank


def test_leaderboard_renderer_returns_png_with_existing_width() -> None:
    png = render_leaderboard({"season": "Test", "entries": []})
    assert png.startswith(b"\x89PNG\r\n\x1a\n")


def test_leaderboard_renderer_rejects_more_than_100_rows() -> None:
    try:
        render_leaderboard({"season": "Test", "entries": [{}] * 101})
    except ValueError as error:
        assert "100" in str(error)
    else:
        raise AssertionError("row limit was not enforced")


def test_leaderboard_renderer_rejects_non_mapping_rows() -> None:
    with pytest.raises(ValueError, match="row"):
        render_leaderboard({"entries": ["not-a-row"]})


@pytest.mark.asyncio
async def test_renderer_service_returns_png_and_rejects_invalid_json_models() -> None:
    png = await render_model({"entries": [], "season": "Test"}, lambda _: b"\x89PNG\r\n\x1a\n")
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    with pytest.raises(ValueError, match="object"):
        await render_model(["not-a-model"], render_leaderboard)


def test_rank_renderer_accepts_the_discord_avatar_bytes_used_by_the_original_card() -> None:
    avatar = Image.new("RGB", (8, 8), "red")
    encoded = BytesIO()
    avatar.save(encoded, format="PNG")

    png = render_rank({
        "username": "Alpha", "rank_current": "Gold", "rank_next": "Platinum",
        "mmr_current": 2000, "mmr_required": 3500, "wins": 4, "losses": 1,
        "avatar_png_base64": base64.b64encode(encoded.getvalue()).decode(),
    })

    assert png.startswith(b"\x89PNG\r\n\x1a\n")


def test_rank_renderer_rejects_a_malformed_avatar_payload() -> None:
    with pytest.raises(ValueError, match="avatar"):
        render_rank({
            "username": "Alpha", "rank_current": "Gold", "rank_next": "Platinum",
            "mmr_current": 2000, "mmr_required": 3500, "wins": 4, "losses": 1,
            "avatar_png_base64": "not base64",
        })


def test_rank_renderer_rejects_an_avatar_with_excessive_pixel_dimensions() -> None:
    avatar = Image.new("RGB", (4_097, 1), "red")
    encoded = BytesIO()
    avatar.save(encoded, format="PNG")

    with pytest.raises(ValueError, match="avatar"):
        render_rank({
            "username": "Alpha", "rank_current": "Gold", "rank_next": "Platinum",
            "mmr_current": 2000, "mmr_required": 3500, "wins": 4, "losses": 1,
            "avatar_png_base64": base64.b64encode(encoded.getvalue()).decode(),
        })


@pytest.mark.asyncio
async def test_renderer_rejects_a_request_when_all_render_slots_are_busy(monkeypatch: pytest.MonkeyPatch) -> None:
    import battlevive_renderer.renderer_service as renderer_service

    monkeypatch.setattr(renderer_service, "_render_slots", asyncio.Semaphore(0))

    with pytest.raises(RenderBusy):
        await render_model({}, lambda _: b"png")


@pytest.mark.asyncio
async def test_renderer_keeps_a_slot_while_a_timed_out_worker_finishes(monkeypatch: pytest.MonkeyPatch) -> None:
    import battlevive_renderer.renderer_service as renderer_service

    monkeypatch.setattr(renderer_service, "_render_slots", asyncio.Semaphore(1))
    monkeypatch.setattr(renderer_service, "_RENDER_TIMEOUT_SECONDS", 0.01)
    started, release = Event(), Event()

    def slow_renderer(_: dict[str, object]) -> bytes:
        started.set()
        release.wait()
        return b"png"

    try:
        with pytest.raises(asyncio.TimeoutError):
            await render_model({}, slow_renderer)
        assert started.is_set()
        with pytest.raises(RenderBusy):
            await render_model({}, lambda _: b"png")
    finally:
        release.set()
    async def wait_for_slot_release() -> None:
        while renderer_service._render_slots.locked():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(wait_for_slot_release(), timeout=1)
    assert not renderer_service._render_slots.locked()

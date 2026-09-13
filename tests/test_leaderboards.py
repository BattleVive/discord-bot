"""Tests for automatic Discord leaderboard rendering and publication."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from battlevive_gateway.leaderboards import GuildLeaderboardPublisher
from battlevive_gateway.leaderboards import LeaderboardService


@pytest.mark.asyncio
async def test_automatic_leaderboard_waits_for_discord_guild_cache() -> None:
    """Verify that automatic leaderboard waits for Discord guild cache."""
    bot = SimpleNamespace(get_guild=lambda _: None)
    publisher = GuildLeaderboardPublisher(bot, object(), object(), object())

    assert not await publisher.reconcile_guild(
        {"guild_id": 10, "leaderboard_channel_id": 20, "leaderboard_limit": 10}
    )


@pytest.mark.asyncio
async def test_automatic_leaderboard_renders_and_publishes_a_configured_channel() -> None:
    """Verify that automatic leaderboard renders and publishes a configured channel."""
    rendered: list[dict[str, object]] = []
    saved: list[tuple[object, ...]] = []

    class Upstream:
        """Provide a upstream test double."""
        async def get_result(self, path: str, *, require_fresh: bool) -> object:
            """Provide get result behavior for the test scenario."""
            assert (path, require_fresh) == ("/leaderboard", True)
            return SimpleNamespace(data={"season": "2026-3", "leaderboard": [
                {"position": 1, "display_name": "Alpha", "discord_id": 7, "member_number": 7, "rank": "Gold", "mmr": 1200, "wins": 4, "losses": 1},
            ]})

    class Renderer:
        """Provide a renderer test double."""
        async def render_leaderboard(self, model: dict[str, object]) -> bytes:
            """Provide render leaderboard behavior for the test scenario."""
            rendered.append(model)
            return b"\x89PNG\r\n\x1a\nrendered"

    class Publications:
        """Provide a publications test double."""
        async def list_for_feature(self, *_: object) -> list[dict[str, object]]:
            """Provide list for feature behavior for the test scenario."""
            return []

        async def upsert(self, *args: object) -> None:
            """Provide upsert behavior for the test scenario."""
            saved.append(args)

    message = SimpleNamespace(id=55)
    channel = SimpleNamespace(send=None)

    async def send(*, file: object) -> object:
        """Provide send behavior for the test scenario."""
        assert file.filename == "leaderboard.png"
        return message

    channel.send = send
    guild = SimpleNamespace(id=10, members=[SimpleNamespace(id=7)], get_channel=lambda channel_id: channel if channel_id == 20 else None)
    bot = SimpleNamespace(get_guild=lambda guild_id: guild if guild_id == 10 else None)
    publisher = GuildLeaderboardPublisher(bot, Upstream(), Renderer(), Publications())

    changed = await publisher.reconcile_guild({"guild_id": 10, "leaderboard_channel_id": 20, "leaderboard_limit": 10})

    assert changed is True
    assert rendered == [{"season": "2026-3", "entries": [{"place": 1, "username": "Alpha", "rank": "Gold", "mmr": 1200, "wins": 4, "losses": 1, "win_rate": 80}]}]
    assert saved[0][2:6] == ("slot:0", 20, 55, None)


@pytest.mark.asyncio
async def test_leaderboard_worker_recovers_after_a_failed_cycle() -> None:
    """Verify that leaderboard worker recovers after a failed cycle."""
    service = LeaderboardService(object(), object(), object(), object(), interval=0.001)
    calls = 0
    retried = asyncio.Event()

    async def reconcile() -> None:
        """Provide reconcile behavior for the test scenario."""
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionError("temporary database outage")
        retried.set()

    service.reconcile_all = reconcile  # type: ignore[method-assign]
    service.start()
    await asyncio.wait_for(retried.wait(), timeout=1)
    await service.stop()

    assert calls >= 2

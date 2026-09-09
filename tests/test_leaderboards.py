"""Tests for automatic Discord leaderboard rendering and publication."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from battlevive_gateway.leaderboards import GuildLeaderboardPublisher
from battlevive_gateway.leaderboards import LeaderboardService


@pytest.mark.asyncio
async def test_automatic_leaderboard_waits_for_discord_guild_cache() -> None:
    bot = SimpleNamespace(get_guild=lambda _: None)
    publisher = GuildLeaderboardPublisher(bot, object(), object(), object())

    assert not await publisher.reconcile_guild(
        {"guild_id": 10, "leaderboard_channel_id": 20, "leaderboard_limit": 10}
    )


@pytest.mark.asyncio
async def test_automatic_leaderboard_renders_and_publishes_a_configured_channel() -> None:
    rendered: list[dict[str, object]] = []
    saved: list[tuple[object, ...]] = []

    class Upstream:
        async def get_result(self, path: str, *, require_fresh: bool) -> object:
            assert (path, require_fresh) == ("/leaderboard", True)
            return SimpleNamespace(data={"season": "Season 3", "leaderboard": [
                {"position": 1, "player": "Alpha", "member_number": 7, "rank": "Gold", "mmr": 1200, "wins": 4, "losses": 1},
            ]})

    class Renderer:
        async def render_leaderboard(self, model: dict[str, object]) -> bytes:
            rendered.append(model)
            return b"\x89PNG\r\n\x1a\nrendered"

    class Publications:
        async def list_for_feature(self, *_: object) -> list[dict[str, object]]:
            return []

        async def upsert(self, *args: object) -> None:
            saved.append(args)

    message = SimpleNamespace(id=55)
    channel = SimpleNamespace(send=None)

    async def send(*, file: object) -> object:
        assert file.filename == "leaderboard.png"
        return message

    channel.send = send
    guild = SimpleNamespace(id=10, get_channel=lambda channel_id: channel if channel_id == 20 else None)
    bot = SimpleNamespace(get_guild=lambda guild_id: guild if guild_id == 10 else None)
    publisher = GuildLeaderboardPublisher(bot, Upstream(), Renderer(), Publications())

    changed = await publisher.reconcile_guild({"guild_id": 10, "leaderboard_channel_id": 20, "leaderboard_limit": 10})

    assert changed is True
    assert rendered == [{"season": "Season 3", "entries": [{"place": 1, "username": "Alpha", "rank": "Gold", "mmr": 1200, "wins": 4, "losses": 1, "win_rate": 80}]}]
    assert saved[0][2:6] == ("slot:0", 20, 55, None)


@pytest.mark.asyncio
async def test_leaderboard_worker_recovers_after_a_failed_cycle() -> None:
    service = LeaderboardService(object(), object(), object(), object(), interval=0.001)
    calls = 0

    async def reconcile() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionError("temporary database outage")

    service.reconcile_all = reconcile  # type: ignore[method-assign]
    service.start()
    await __import__("asyncio").sleep(0.01)
    await service.stop()

    assert calls >= 2

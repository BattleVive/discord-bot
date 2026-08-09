from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip("discord")
pytest.importorskip("asyncpg")

from battlevive_bot import bot as bot_module
from battlevive_bot.refresh import RefreshCoordinator
from battlevive_bot.refresh import RefreshResult


class FakeClient:
    def __init__(self, users=None, lobbies=None, ratings=None) -> None:
        self.users = users or []
        self.lobbies = lobbies or []
        self.ratings = ratings or []
        self.calls: list[str] = []

    async def get_users(self):
        self.calls.append("users")
        return self.users

    async def get_lobbies(self):
        self.calls.append("lobbies")
        return self.lobbies

    async def get_season_ratings(self):
        self.calls.append("ratings")
        return self.ratings

    async def refresh_credentials(self):
        self.calls.append("refresh")


@pytest.mark.asyncio
async def test_setup_starts_services_and_refresh_loops(monkeypatch) -> None:
    started: list[str] = []

    async def init_pool(dsn):
        return None

    class Service:
        def __init__(self, *args):
            pass

        def start(self):
            started.append("service")

    async def sync_tree(*args, **kwargs):
        return []

    monkeypatch.setattr(bot_module, "init_pool", init_pool)
    monkeypatch.setattr(bot_module, "LeaderboardService", Service)
    monkeypatch.setattr(bot_module, "ActiveLobbyService", Service)
    monkeypatch.setattr(bot_module.revalidate_tokens, "start", lambda: started.append("tokens"))
    monkeypatch.setattr(bot_module.refresh_infrequently_changing_data, "start", lambda: started.append("hourly"))
    monkeypatch.setattr(bot_module.refresh_frequently_changing_data, "start", lambda: started.append("frequent"))
    monkeypatch.setattr(bot_module.bot.tree, "copy_global_to", lambda **kwargs: None)
    monkeypatch.setattr(bot_module.bot.tree, "sync", sync_tree)
    await bot_module.setup_hook()
    assert started == ["service", "service", "tokens", "hourly", "frequent"]
    assert bot_module.bot.command_access_service is not None


@pytest.mark.asyncio
async def test_scheduled_token_refresh_allows_later_cycles(monkeypatch) -> None:
    client = FakeClient()
    monkeypatch.setattr(bot_module, "battlevive_client", client)
    await bot_module.revalidate_tokens.coro()
    await bot_module.revalidate_tokens.coro()
    assert client.calls == ["refresh", "refresh"]


@pytest.mark.asyncio
async def test_hourly_refresh_publishes_only_after_database_success(monkeypatch) -> None:
    users = [object()]
    published: list[RefreshResult] = []
    coordinator = RefreshCoordinator(FakeClient(users=users), published.append)

    async def sync(fetched):
        assert fetched is users
        return []

    monkeypatch.setattr("battlevive_bot.refresh.db.sync_users_to_db", sync)
    await coordinator.hourly_users_refresh()
    assert published[0].users is users


@pytest.mark.asyncio
async def test_miss_refreshes_are_coalesced_for_thirty_seconds(monkeypatch) -> None:
    client = FakeClient(users=[], ratings=[])
    coordinator = RefreshCoordinator(client)

    async def sync_users(users):
        return []

    async def sync_ratings(ratings):
        return None

    monkeypatch.setattr("battlevive_bot.refresh.db.sync_users_to_db", sync_users)
    monkeypatch.setattr("battlevive_bot.refresh.db.sync_season_ratings_to_db", sync_ratings)
    first, second = await asyncio.gather(
        coordinator.users_and_ratings_refresh(),
        coordinator.users_and_ratings_refresh(),
    )
    assert client.calls.count("users") == 1
    assert client.calls.count("ratings") == 1
    assert second.coalesced or first.coalesced


@pytest.mark.asyncio
async def test_manual_refresh_is_limited_to_invoking_guild(monkeypatch) -> None:
    calls: list[object] = []

    class Coordinator:
        lock = asyncio.Lock()

        async def full_manual_refresh(self):
            calls.append("refresh")

    async def ranks(bot, *, guild):
        calls.append(guild)

    monkeypatch.setattr(bot_module.bot, "refresh_coordinator", Coordinator())
    monkeypatch.setattr(bot_module, "give_rank_roles", ranks)
    monkeypatch.setattr(bot_module.bot, "leaderboard_service", None)
    monkeypatch.setattr(bot_module.bot, "active_lobby_service", None)
    messages: list[str] = []

    class Response:
        async def defer(self, *, ephemeral):
            pass

    class Followup:
        async def send(self, message, *, ephemeral):
            messages.append(message)

    guild = SimpleNamespace(id=123)
    interaction = SimpleNamespace(
        guild=guild,
        user=SimpleNamespace(guild_permissions=SimpleNamespace(manage_guild=True)),
        response=Response(),
        followup=Followup(),
    )
    await bot_module.refresh.callback(interaction)
    assert calls == ["refresh", guild]
    assert messages == ["Battlevive data refreshed."]


@pytest.mark.asyncio
async def test_member_join_passes_miss_refresh_callback(monkeypatch) -> None:
    callbacks = []

    async def reconcile(member, refresh_on_miss):
        callbacks.append(refresh_on_miss)

    monkeypatch.setattr(bot_module, "reconcile_member_roles", reconcile)
    monkeypatch.setattr(bot_module.bot, "leaderboard_service", None)
    await bot_module.on_member_join(SimpleNamespace(id=1, guild=SimpleNamespace(id=2)))
    assert len(callbacks) == 1

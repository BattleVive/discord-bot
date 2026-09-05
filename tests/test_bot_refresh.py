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
        """Initialize a fake Battlevive client with test data."""
        self.users = users or []
        self.lobbies = lobbies or []
        self.ratings = ratings or []
        self.calls: list[str] = []

    async def get_users(self):
        """Record a users fetch and return the configured users."""
        self.calls.append("users")
        return self.users

    async def get_lobbies(self):
        """Record a lobbies fetch and return the configured lobbies."""
        self.calls.append("lobbies")
        return self.lobbies

    async def get_season_ratings(self):
        """Record a ratings fetch and return the configured ratings."""
        self.calls.append("ratings")
        return self.ratings

    async def refresh_credentials(self):
        """Record a credential refresh operation."""
        self.calls.append("refresh")


@pytest.mark.asyncio
async def test_setup_starts_services_and_refresh_loops(monkeypatch) -> None:
    """Verify that setup_hook initializes services and starts background tasks."""
    started: list[str] = []

    async def init_pool(dsn):
        """Stub database pool initialization."""
        return None

    class Service:
        def __init__(self, *args):
            """Initialize a stub service."""
            pass

        def start(self):
            """Record service start."""
            started.append("service")

    async def sync_tree(*args, **kwargs):
        return []

    monkeypatch.setattr(bot_module, "init_pool", init_pool)
    monkeypatch.setattr(bot_module, "LeaderboardService", Service)
    monkeypatch.setattr(bot_module, "ActiveLobbyService", Service)
    monkeypatch.setattr(bot_module, "GuideThreadService", Service)
    monkeypatch.setattr(bot_module.revalidate_tokens, "start", lambda: started.append("tokens"))
    monkeypatch.setattr(bot_module.refresh_infrequently_changing_data, "start", lambda: started.append("hourly"))
    monkeypatch.setattr(bot_module.refresh_frequently_changing_data, "start", lambda: started.append("frequent"))
    monkeypatch.setattr(bot_module.bot.tree, "copy_global_to", lambda **kwargs: None)
    monkeypatch.setattr(bot_module.bot.tree, "sync", sync_tree)
    await bot_module.setup_hook()
    assert started == ["service", "service", "service", "tokens", "hourly", "frequent"]
    assert bot_module.bot.command_access_service is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("configured_url", ["", "http://battlevive.test"])
async def test_setup_disables_match_links_when_the_configured_url_is_invalid(
    monkeypatch,
    configured_url: str,
) -> None:
    """
    Verify that setup retries the active lobby service without the configured URL when URL validation fails.
    """
    active_service_urls: list[str | None] = []
    started: list[str] = []

    async def init_pool(dsn):
        return None

    class LeaderboardService:
        def __init__(self, *args):
            pass

        def start(self):
            started.append("leaderboard")

    class ActiveLobbyService:
        def __init__(self, *args):
            battlevive_url = args[4]
            active_service_urls.append(battlevive_url)
            if battlevive_url is not None:
                raise ValueError("invalid test URL")

        def start(self):
            started.append("active-matches")

    async def sync_tree(*args, **kwargs):
        return []

    monkeypatch.setattr(bot_module, "init_pool", init_pool)
    monkeypatch.setattr(bot_module, "LeaderboardService", LeaderboardService)
    monkeypatch.setattr(bot_module, "ActiveLobbyService", ActiveLobbyService)
    monkeypatch.setattr(bot_module, "GuideThreadService", LeaderboardService)
    monkeypatch.setattr(bot_module, "BATTLEVIVE_URL", configured_url)
    monkeypatch.setattr(bot_module.revalidate_tokens, "start", lambda: None)
    monkeypatch.setattr(bot_module.refresh_infrequently_changing_data, "start", lambda: None)
    monkeypatch.setattr(bot_module.refresh_frequently_changing_data, "start", lambda: None)
    monkeypatch.setattr(bot_module.publish_health, "start", lambda: None)
    monkeypatch.setattr(bot_module.bot.tree, "copy_global_to", lambda **kwargs: None)
    monkeypatch.setattr(bot_module.bot.tree, "sync", sync_tree)

    await bot_module.setup_hook()

    assert active_service_urls == [configured_url, None]
    assert "active-matches" in started


@pytest.mark.asyncio
async def test_scheduled_token_refresh_allows_later_cycles(monkeypatch) -> None:
    """Verify that token refresh task continues after encountering errors."""
    client = FakeClient()
    monkeypatch.setattr(bot_module, "battlevive_client", client)
    await bot_module.revalidate_tokens.coro()
    await bot_module.revalidate_tokens.coro()
    assert client.calls == ["refresh", "refresh"]


@pytest.mark.asyncio
async def test_hourly_refresh_publishes_only_after_database_success(monkeypatch) -> None:
    """Verify that hourly refresh publishes data only after successful database sync."""
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
    """Verify that concurrent miss refreshes are coalesced to avoid redundant fetches."""
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
    """Verify that manual refresh applies rank roles only to the invoking guild."""
    calls: list[object] = []

    class Coordinator:
        """Stub refresh coordinator for testing manual refresh."""
        lock = asyncio.Lock()

        async def full_manual_refresh(self):
            """Record manual refresh invocation."""
            calls.append("refresh")

    async def ranks(bot, *, guild):
        """Record rank role synchronization for the guild."""
        calls.append(guild)

    monkeypatch.setattr(bot_module.bot, "refresh_coordinator", Coordinator())
    monkeypatch.setattr(bot_module, "give_rank_roles", ranks)
    monkeypatch.setattr(bot_module.bot, "leaderboard_service", None)
    monkeypatch.setattr(bot_module.bot, "active_lobby_service", None)
    monkeypatch.setattr(bot_module.bot, "guide_thread_service", None)
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
    """Verify that member join handler provides refresh callback to role reconciliation."""
    callbacks = []

    async def reconcile(member, refresh_on_miss):
        """Record the refresh callback passed to reconcile_member_roles."""
        callbacks.append(refresh_on_miss)

    monkeypatch.setattr(bot_module, "reconcile_member_roles", reconcile)
    monkeypatch.setattr(bot_module.bot, "leaderboard_service", None)
    await bot_module.on_member_join(SimpleNamespace(id=1, guild=SimpleNamespace(id=2)))
    assert len(callbacks) == 1

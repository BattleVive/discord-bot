from __future__ import annotations

from types import SimpleNamespace

import pytest

discord = pytest.importorskip("discord")
pytest.importorskip("asyncpg")

from battlevive_bot import bot as bot_module
from battlevive_bot.db import MissingUsersError


class FakeClient:
    def __init__(
        self,
        *,
        users: list[object] | None = None,
        lobbies: list[object] | None = None,
        ratings: list[object] | None = None,
    ) -> None:
        self.users = users or []
        self.lobbies = lobbies or []
        self.ratings = ratings or []
        self.calls: list[str] = []

    async def get_users(self) -> list[object]:
        self.calls.append("users")
        return self.users

    async def get_lobbies(self) -> list[object]:
        self.calls.append("lobbies")
        return self.lobbies

    async def get_season_ratings(self) -> list[object]:
        self.calls.append("ratings")
        return self.ratings

    async def refresh_credentials(self) -> None:
        self.calls.append("refresh")

    async def close(self) -> None:
        self.calls.append("close")


@pytest.mark.asyncio
async def test_setup_starts_token_and_data_refresh_loops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: list[str] = []

    async def init_pool(dsn: str | None) -> None:
        return None

    class LeaderboardService:
        def __init__(self, *args: object) -> None:
            return None

        def start(self) -> None:
            started.append("leaderboard")

    class ActiveLobbyService:
        def __init__(self, *args: object) -> None:
            return None

        def start(self) -> None:
            started.append("active-lobbies")

    async def sync_tree(*args: object, **kwargs: object) -> list[object]:
        return []

    monkeypatch.setattr(bot_module, "init_pool", init_pool)
    monkeypatch.setattr(bot_module, "LeaderboardService", LeaderboardService)
    monkeypatch.setattr(bot_module, "ActiveLobbyService", ActiveLobbyService)
    monkeypatch.setattr(
        bot_module.revalidate_tokens,
        "start",
        lambda: started.append("tokens"),
    )
    monkeypatch.setattr(
        bot_module.refresh_infrequently_changing_data,
        "start",
        lambda: started.append("infrequent"),
    )
    monkeypatch.setattr(
        bot_module.refresh_frequently_changing_data,
        "start",
        lambda: started.append("frequent"),
    )
    monkeypatch.setattr(bot_module.bot.tree, "copy_global_to", lambda **kwargs: None)
    monkeypatch.setattr(bot_module.bot.tree, "sync", sync_tree)
    monkeypatch.setattr(bot_module.bot, "leaderboard_service", None)
    monkeypatch.setattr(bot_module.bot, "active_lobby_service", None)

    await bot_module.setup_hook()

    assert started == [
        "leaderboard",
        "active-lobbies",
        "tokens",
        "infrequent",
        "frequent",
    ]


@pytest.mark.asyncio
async def test_scheduled_refresh_delegates_to_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient()
    monkeypatch.setattr(bot_module, "battlevive_client", client)

    await bot_module.revalidate_tokens.coro()

    assert client.calls == ["refresh"]


@pytest.mark.asyncio
async def test_scheduled_refresh_handles_error_and_allows_next_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def refresh_credentials() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary failure")

    client = SimpleNamespace(refresh_credentials=refresh_credentials)
    monkeypatch.setattr(bot_module, "battlevive_client", client)

    await bot_module.revalidate_tokens.coro()
    await bot_module.revalidate_tokens.coro()

    assert calls == 2


@pytest.mark.asyncio
async def test_infrequent_refresh_fetches_syncs_then_assigns_roles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    users = [object()]
    client = FakeClient(users=users)
    events: list[object] = []

    async def sync_data(**kwargs: object) -> None:
        events.append(("sync", kwargs))

    async def give_player_role(*args: object) -> None:
        events.append("player-role")

    monkeypatch.setattr(bot_module, "battlevive_client", client)
    monkeypatch.setattr(bot_module, "sync_battlevive_data_to_db", sync_data)
    monkeypatch.setattr(bot_module, "give_battlevive_role", give_player_role)
    monkeypatch.setattr(bot_module, "battlevive_users", [])

    await bot_module.refresh_infrequently_changing_data.coro()

    assert bot_module.refresh_infrequently_changing_data.hours == 1
    assert bot_module.battlevive_users is users
    assert client.calls == ["users"]
    assert events == [("sync", {"users": users}), "player-role"]


@pytest.mark.asyncio
async def test_frequent_refresh_fetches_lobbies_and_ratings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetched_lobbies = [object()]
    fetched_ratings = [object()]
    client = FakeClient(lobbies=fetched_lobbies, ratings=fetched_ratings)
    sync_calls: list[dict[str, object]] = []
    role_calls: list[str] = []

    async def sync_data(**kwargs: object) -> None:
        sync_calls.append(kwargs)

    async def give_rank_role(*args: object) -> None:
        role_calls.append("rank")

    monkeypatch.setattr(bot_module, "battlevive_client", client)
    monkeypatch.setattr(bot_module, "sync_battlevive_data_to_db", sync_data)
    monkeypatch.setattr(bot_module, "give_rank_roles", give_rank_role)
    monkeypatch.setattr(bot_module, "lobbies", [])
    monkeypatch.setattr(bot_module, "season_ratings", [])
    requested: list[str] = []
    monkeypatch.setattr(
        bot_module.bot,
        "active_lobby_service",
        SimpleNamespace(request_reconciliation=lambda: requested.append("active")),
    )

    await bot_module.refresh_frequently_changing_data.coro()

    assert bot_module.refresh_frequently_changing_data.seconds == 30
    assert bot_module.lobbies is fetched_lobbies
    assert bot_module.season_ratings is fetched_ratings
    assert sorted(client.calls) == ["lobbies", "ratings"]
    assert sync_calls == [
        {
            "lobbies": fetched_lobbies,
            "season_ratings": fetched_ratings,
        }
    ]
    assert role_calls == ["rank"]
    assert requested == ["active"]


@pytest.mark.asyncio
async def test_frequent_refresh_fetches_users_and_retries_on_missing_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    users = [object()]
    fetched_lobbies = [object()]
    fetched_ratings = [object()]
    client = FakeClient(
        users=users,
        lobbies=fetched_lobbies,
        ratings=fetched_ratings,
    )
    sync_calls: list[dict[str, object]] = []
    role_calls: list[str] = []

    async def sync_data(**kwargs: object) -> None:
        sync_calls.append(kwargs)
        if len(sync_calls) == 1:
            raise MissingUsersError

    async def give_player_role(*args: object) -> None:
        role_calls.append("player")

    async def give_rank_role(*args: object) -> None:
        role_calls.append("rank")

    monkeypatch.setattr(bot_module, "battlevive_client", client)
    monkeypatch.setattr(bot_module, "sync_battlevive_data_to_db", sync_data)
    monkeypatch.setattr(bot_module, "give_battlevive_role", give_player_role)
    monkeypatch.setattr(bot_module, "give_rank_roles", give_rank_role)
    monkeypatch.setattr(bot_module, "battlevive_users", [])

    await bot_module.refresh_frequently_changing_data.coro()

    assert sync_calls == [
        {
            "lobbies": fetched_lobbies,
            "season_ratings": fetched_ratings,
        },
        {
            "users": users,
            "lobbies": fetched_lobbies,
            "season_ratings": fetched_ratings,
        },
    ]
    assert sorted(client.calls[:2]) == ["lobbies", "ratings"]
    assert client.calls[2:] == ["users"]
    assert bot_module.battlevive_users is users
    assert role_calls == ["player", "rank"]


@pytest.mark.asyncio
async def test_manual_refresh_preserves_result_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    users = [object()]
    fetched_lobbies = [object()]
    fetched_ratings = [object()]
    client = FakeClient(
        users=users,
        lobbies=fetched_lobbies,
        ratings=fetched_ratings,
    )
    synced: list[tuple[object, ...]] = []
    role_guilds: list[object] = []
    messages: list[tuple[str, bool]] = []

    async def sync_data(*args: object) -> None:
        synced.append(args)

    async def record_roles(*args: object, **kwargs: object) -> None:
        role_guilds.append(kwargs["guild"])

    class Response:
        async def defer(self, *, ephemeral: bool) -> None:
            assert ephemeral is True

    class Followup:
        async def send(self, message: str, *, ephemeral: bool) -> None:
            messages.append((message, ephemeral))

    monkeypatch.setattr(bot_module, "battlevive_client", client)
    monkeypatch.setattr(bot_module, "sync_battlevive_data_to_db", sync_data)
    monkeypatch.setattr(bot_module, "give_battlevive_role", record_roles)
    monkeypatch.setattr(bot_module, "give_rank_roles", record_roles)
    monkeypatch.setattr(bot_module.bot, "leaderboard_service", None)
    monkeypatch.setattr(bot_module.bot, "active_lobby_service", None)
    guild = SimpleNamespace(id=123)
    user = SimpleNamespace(
        guild_permissions=SimpleNamespace(manage_guild=True),
    )
    interaction = SimpleNamespace(
        response=Response(),
        followup=Followup(),
        guild=guild,
        user=user,
    )

    await bot_module.refresh.callback(interaction)

    assert synced == [(users, fetched_lobbies, fetched_ratings)]
    assert role_guilds == [guild, guild]
    assert messages == [("Battlevive data refreshed.", True)]


@pytest.mark.asyncio
async def test_manual_refresh_rejects_overlap_without_upstream_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient()
    monkeypatch.setattr(bot_module, "battlevive_client", client)
    messages: list[dict[str, object]] = []

    class Response:
        async def send_message(self, content: str, **kwargs: object) -> None:
            messages.append({"content": content, **kwargs})

    interaction = SimpleNamespace(
        guild=SimpleNamespace(id=1),
        user=SimpleNamespace(
            guild_permissions=SimpleNamespace(manage_guild=True),
        ),
        response=Response(),
    )
    await bot_module._manual_refresh_lock.acquire()
    try:
        await bot_module.refresh.callback(interaction)
    finally:
        bot_module._manual_refresh_lock.release()

    assert client.calls == []
    assert messages[0]["content"].startswith("A manual refresh is already running")


@pytest.mark.asyncio
async def test_member_join_uses_only_targeted_local_reconciliation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reconciled: list[object] = []
    client = FakeClient()

    async def reconcile(member: object) -> None:
        reconciled.append(member)

    monkeypatch.setattr(bot_module, "battlevive_client", client)
    monkeypatch.setattr(bot_module, "reconcile_member_roles", reconcile)
    monkeypatch.setattr(bot_module.bot, "leaderboard_service", None)
    member = SimpleNamespace(
        id=123,
        guild=SimpleNamespace(id=456),
    )

    await bot_module.on_member_join(member)

    assert reconciled == [member]
    assert client.calls == []


@pytest.mark.asyncio
async def test_shutdown_closes_client_and_runtime_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient()
    closed: list[str] = []

    async def close_pool() -> None:
        closed.append("pool")

    async def close_discord(self: object) -> None:
        closed.append("discord")

    monkeypatch.setattr(bot_module, "battlevive_client", client)
    monkeypatch.setattr(bot_module, "close_pool", close_pool)
    monkeypatch.setattr(bot_module.commands.Bot, "close", close_discord)
    monkeypatch.setattr(bot_module.revalidate_tokens, "is_running", lambda: False)
    monkeypatch.setattr(
        bot_module.refresh_infrequently_changing_data,
        "is_running",
        lambda: False,
    )
    monkeypatch.setattr(
        bot_module.refresh_frequently_changing_data,
        "is_running",
        lambda: False,
    )
    instance = bot_module.BattleviveBot(
        command_prefix="!",
        intents=discord.Intents.none(),
    )

    await instance.close()

    assert client.calls == ["close"]
    assert closed == ["pool", "discord"]

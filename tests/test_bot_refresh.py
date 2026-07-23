from __future__ import annotations

from types import SimpleNamespace

import pytest

discord = pytest.importorskip("discord")
pytest.importorskip("asyncpg")

from battlevive_bot import bot as bot_module
from battlevive_bot.db import MissingUsersError


@pytest.mark.asyncio
async def test_setup_starts_both_data_refresh_loops(
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

    async def sync_tree(*args: object, **kwargs: object) -> list[object]:
        return []

    monkeypatch.setattr(bot_module, "init_pool", init_pool)
    monkeypatch.setattr(bot_module, "LeaderboardService", LeaderboardService)
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

    await bot_module.setup_hook()

    assert started == ["leaderboard", "tokens", "infrequent", "frequent"]


@pytest.mark.asyncio
async def test_infrequent_refresh_only_fetches_and_syncs_users(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    users = [object()]
    sync_calls: list[dict[str, object]] = []
    role_calls: list[str] = []

    async def query_users(token: str | None) -> list[object]:
        assert token == bot_module.battlevive_tokens.JWT_token
        return users

    async def sync_data(**kwargs: object) -> None:
        sync_calls.append(kwargs)

    async def give_player_role(*args: object) -> None:
        role_calls.append("player")

    monkeypatch.setattr(bot_module, "query_users", query_users)
    monkeypatch.setattr(bot_module, "sync_battlevive_data_to_db", sync_data)
    monkeypatch.setattr(bot_module, "give_battlevive_role", give_player_role)
    monkeypatch.setattr(bot_module, "battlevive_users", [])

    await bot_module.refresh_infrequently_changing_data.coro()

    assert bot_module.refresh_infrequently_changing_data.hours == 1
    assert bot_module.battlevive_users is users
    assert sync_calls == [{"users": users}]
    assert role_calls == ["player"]


@pytest.mark.asyncio
async def test_frequent_refresh_only_fetches_lobbies_and_ratings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetched_lobbies = [object()]
    fetched_ratings = [object()]
    sync_calls: list[dict[str, object]] = []
    role_calls: list[str] = []

    async def query_lobbies(token: str | None) -> list[object]:
        return fetched_lobbies

    async def query_ratings(token: str | None) -> list[object]:
        return fetched_ratings

    async def sync_data(**kwargs: object) -> None:
        sync_calls.append(kwargs)

    async def give_rank_role(*args: object) -> None:
        role_calls.append("rank")

    monkeypatch.setattr(bot_module, "query_lobbies", query_lobbies)
    monkeypatch.setattr(bot_module, "query_season_ratings", query_ratings)
    monkeypatch.setattr(bot_module, "sync_battlevive_data_to_db", sync_data)
    monkeypatch.setattr(bot_module, "give_rank_roles", give_rank_role)
    monkeypatch.setattr(bot_module, "lobbies", [])
    monkeypatch.setattr(bot_module, "season_ratings", [])

    await bot_module.refresh_frequently_changing_data.coro()

    assert bot_module.refresh_frequently_changing_data.seconds == 30
    assert bot_module.lobbies is fetched_lobbies
    assert bot_module.season_ratings is fetched_ratings
    assert sync_calls == [
        {
            "lobbies": fetched_lobbies,
            "season_ratings": fetched_ratings,
        }
    ]
    assert role_calls == ["rank"]


@pytest.mark.asyncio
async def test_frequent_refresh_fetches_users_and_retries_on_missing_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    users = [object()]
    fetched_lobbies = [object()]
    fetched_ratings = [object()]
    sync_calls: list[dict[str, object]] = []
    role_calls: list[str] = []

    async def query_users(token: str | None) -> list[object]:
        return users

    async def query_lobbies(token: str | None) -> list[object]:
        return fetched_lobbies

    async def query_ratings(token: str | None) -> list[object]:
        return fetched_ratings

    async def sync_data(**kwargs: object) -> None:
        sync_calls.append(kwargs)
        if len(sync_calls) == 1:
            raise MissingUsersError

    async def give_player_role(*args: object) -> None:
        role_calls.append("player")

    async def give_rank_role(*args: object) -> None:
        role_calls.append("rank")

    monkeypatch.setattr(bot_module, "query_users", query_users)
    monkeypatch.setattr(bot_module, "query_lobbies", query_lobbies)
    monkeypatch.setattr(bot_module, "query_season_ratings", query_ratings)
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
    assert bot_module.battlevive_users is users
    assert role_calls == ["player", "rank"]


@pytest.mark.asyncio
async def test_manual_refresh_preserves_query_result_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    users = [object()]
    fetched_lobbies = [object()]
    fetched_ratings = [object()]
    synced: list[tuple[object, ...]] = []
    messages: list[tuple[str, bool]] = []

    async def query_users(token: str | None) -> list[object]:
        return users

    async def query_lobbies(token: str | None) -> list[object]:
        return fetched_lobbies

    async def query_ratings(token: str | None) -> list[object]:
        return fetched_ratings

    async def sync_data(*args: object) -> None:
        synced.append(args)

    async def no_op(*args: object) -> None:
        return None

    class Response:
        async def defer(self, *, ephemeral: bool) -> None:
            assert ephemeral is True

    class Followup:
        async def send(self, message: str, *, ephemeral: bool) -> None:
            messages.append((message, ephemeral))

    monkeypatch.setattr(bot_module, "query_users", query_users)
    monkeypatch.setattr(bot_module, "query_lobbies", query_lobbies)
    monkeypatch.setattr(bot_module, "query_season_ratings", query_ratings)
    monkeypatch.setattr(bot_module, "sync_battlevive_data_to_db", sync_data)
    monkeypatch.setattr(bot_module, "give_battlevive_role", no_op)
    monkeypatch.setattr(bot_module, "give_rank_roles", no_op)
    monkeypatch.setattr(bot_module.bot, "leaderboard_service", None)
    interaction = SimpleNamespace(response=Response(), followup=Followup())

    await bot_module.refresh.callback(interaction)

    assert synced == [(users, fetched_lobbies, fetched_ratings)]
    assert messages == [("Battlevive data refreshed.", True)]

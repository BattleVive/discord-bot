from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime
from datetime import timedelta
from datetime import timezone
import os
from pathlib import Path
from urllib.parse import urlparse
import uuid

import asyncpg
import pytest
import pytest_asyncio

from battlevive_bot import db
from battlevive_bot.models import Lobby
from battlevive_bot.models import SeasonRating
from battlevive_bot.models import User
from tests.factories import USER_A_ID
from tests.factories import USER_B_ID
from tests.factories import lobby_payload
from tests.factories import season_rating_payload
from tests.factories import user_payload


ROOT_DIR = Path(__file__).resolve().parents[1]
SQL_DIR = ROOT_DIR / "app" / "init-db"
TABLES = {
    "users",
    "season_ratings",
    "lobbies",
    "lobby_rosters",
    "guild_config",
    "leaderboard_slots",
}


def get_test_database_url() -> str:
    dsn = os.environ.get("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is not set; skipping PostgreSQL integration tests")

    parsed = urlparse(dsn)
    database = parsed.path.lstrip("/")
    if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        pytest.skip("TEST_DATABASE_URL must point at localhost")
    if "test" not in database.lower():
        pytest.skip("TEST_DATABASE_URL database name must contain 'test'")

    return dsn


async def reset_database(conn: asyncpg.Connection) -> None:
    await conn.execute(
        "DROP TABLE IF EXISTS leaderboard_slots, guild_config, lobby_rosters, "
        "season_ratings, lobbies, users CASCADE"
    )
    for sql_file in sorted(SQL_DIR.glob("*.sql")):
        await conn.execute(sql_file.read_text(encoding="utf-8"))


async def drop_database_tables(conn: asyncpg.Connection) -> None:
    await conn.execute(
        "DROP TABLE IF EXISTS leaderboard_slots, guild_config, lobby_rosters, "
        "season_ratings, lobbies, users CASCADE"
    )


@pytest_asyncio.fixture
async def postgres_db() -> AsyncIterator[None]:
    dsn = get_test_database_url()
    try:
        conn = await asyncpg.connect(dsn=dsn, timeout=2)
    except (OSError, asyncpg.PostgresError):
        if os.environ.get("CI"):
            raise
        pytest.skip("PostgreSQL test database is not reachable")

    try:
        await reset_database(conn)
    finally:
        await conn.close()

    await db.init_pool(dsn)
    try:
        yield
    finally:
        await db.close_pool()
        cleanup_conn = await asyncpg.connect(dsn=dsn, timeout=2)
        try:
            await drop_database_tables(cleanup_conn)
        finally:
            await cleanup_conn.close()


@pytest.mark.asyncio
async def test_init_db_sql_creates_expected_schema(postgres_db: None) -> None:
    pool = db.get_pool()
    table_names = {
        row["table_name"]
        for row in await pool.fetch(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
            """
        )
    }

    assert TABLES.issubset(table_names)

    pool = db.get_pool()
    assert await pool.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'guild_config'
              AND column_name = 'leaderboard_limit'
        )
        """
    )
    trigger_names = {
        row["trigger_name"]
        for row in await pool.fetch(
            """
            SELECT trigger_name
            FROM information_schema.triggers
            WHERE event_object_table IN ('season_ratings', 'users')
            """
        )
    }
    assert {
        "season_ratings_leaderboard_insert",
        "season_ratings_leaderboard_update",
        "season_ratings_leaderboard_delete",
        "users_leaderboard_link_update",
    }.issubset(trigger_names)


@pytest.mark.asyncio
async def test_sync_upserts_in_dependency_order_and_preserves_bot_owned_discord_id(
    postgres_db: None,
) -> None:
    user_a = User.from_dict(user_payload(discord_id=None))
    user_b = User.from_dict(
        user_payload(
            id=USER_B_ID,
            discord_username="PlayerTwo",
            discord_id=None,
            member_number=8,
        )
    )
    lobby = Lobby.from_dict(
        lobby_payload(
            creator_id=USER_A_ID,
            team_one_roster=[USER_A_ID],
            team_two_roster=[USER_B_ID],
        )
    )
    rating = SeasonRating.from_dict(season_rating_payload(user_id=USER_A_ID, mmr=1999))

    await db.sync_battlevive_data_to_db([user_a, user_b], [lobby], [rating])

    pool = db.get_pool()
    bot_owned_discord_id = 987654321012345678
    await pool.execute(
        "UPDATE users SET discord_id = $1 WHERE id = $2",
        bot_owned_discord_id,
        uuid.UUID(USER_A_ID),
    )

    updated_user_a = User.from_dict(
        user_payload(
            discord_id="111111111111111111",
            discord_username="PlayerOneRenamed",
            member_number=70,
            tournaments_joined=9,
        )
    )
    updated_lobby = Lobby.from_dict(
        lobby_payload(
            title="Evening Scrim Updated",
            status="finished",
            selected_map="Ruins",
            team_one_roster=[USER_B_ID],
            team_two_roster=[],
        )
    )
    updated_rating = SeasonRating.from_dict(
        season_rating_payload(
            user_id=USER_A_ID,
            mmr=2500,
            wins=10,
            losses=5,
            matches_played=15,
        )
    )

    await db.sync_battlevive_data_to_db(
        [updated_user_a, user_b],
        [updated_lobby],
        [updated_rating],
    )

    user_row = await pool.fetchrow(
        """
        SELECT discord_username, discord_id, member_number, tournaments_joined
        FROM users
        WHERE id = $1
        """,
        uuid.UUID(USER_A_ID),
    )
    assert dict(user_row) == {
        "discord_username": "PlayerOneRenamed",
        "discord_id": bot_owned_discord_id,
        "member_number": 70,
        "tournaments_joined": 9,
    }

    rating_row = await pool.fetchrow(
        "SELECT mmr, wins, losses, matches_played FROM season_ratings WHERE id = $1",
        501,
    )
    assert dict(rating_row) == {
        "mmr": 2500,
        "wins": 10,
        "losses": 5,
        "matches_played": 15,
    }

    lobby_row = await pool.fetchrow(
        "SELECT title, status, selected_map FROM lobbies WHERE id = $1",
        101,
    )
    assert dict(lobby_row) == {
        "title": "Evening Scrim Updated",
        "status": "finished",
        "selected_map": "Ruins",
    }

    rosters = await pool.fetch(
        """
        SELECT user_id::text, team
        FROM lobby_rosters
        WHERE lobby_id = $1
        ORDER BY team, user_id
        """,
        101,
    )
    assert [(row["user_id"], row["team"]) for row in rosters] == [
        (USER_B_ID, "team_one")
    ]

    assert await pool.fetchval("SELECT COUNT(*) FROM users") == 2
    assert await pool.fetchval("SELECT COUNT(*) FROM lobbies") == 1
    assert await pool.fetchval("SELECT COUNT(*) FROM season_ratings") == 1


@pytest.mark.asyncio
async def test_partial_sync_reports_missing_users(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_with_foreign_key(items: list[object]) -> None:
        raise asyncpg.ForeignKeyViolationError

    async def successful_sync(items: list[object]) -> None:
        return None

    monkeypatch.setattr(db, "sync_lobbies_to_db", fail_with_foreign_key)
    monkeypatch.setattr(db, "sync_season_ratings_to_db", successful_sync)

    with pytest.raises(db.MissingUsersError):
        await db.sync_battlevive_data_to_db(
            lobbies=[object()],
            season_ratings=[object()],
        )


@pytest.mark.asyncio
async def test_guild_config_is_isolated_and_can_be_upserted_and_reset(
    postgres_db: None,
) -> None:
    await db.upsert_guild_config(1001, 2001, 3001)
    await db.upsert_guild_config(1002, 2002, 3002)

    await db.upsert_guild_config(1001, 2011, 3011)

    assert await db.get_guild_config(1001) == {
        "guild_id": 1001,
        "leaderboard_channel_id": 2011,
        "leaderboard_limit": None,
        "updated_by": 3011,
    }
    assert await db.get_guild_config(1002) == {
        "guild_id": 1002,
        "leaderboard_channel_id": 2002,
        "leaderboard_limit": None,
        "updated_by": 3002,
    }

    await db.reset_guild_config(1001, 3021)

    assert await db.get_guild_config(1001) == {
        "guild_id": 1001,
        "leaderboard_channel_id": None,
        "leaderboard_limit": None,
        "updated_by": 3021,
    }


@pytest.mark.asyncio
async def test_leaderboard_limit_and_slot_cache_are_isolated_by_guild(
    postgres_db: None,
) -> None:
    await db.upsert_guild_config(1001, 2001, 3001)
    await db.upsert_guild_config(1002, 2002, 3002)
    await db.set_leaderboard_limit(1001, 5, 3011)
    await db.upsert_leaderboard_slot(
        1001, 0, 2001, 4001, 2026, 7, None, "header-a", b"png-a"
    )
    await db.upsert_leaderboard_slot(
        1002, 0, 2002, 4002, 2026, 7, None, "header-b", b"png-b"
    )

    assert (await db.get_guild_config(1001))["leaderboard_limit"] == 5
    assert (await db.get_guild_config(1002))["leaderboard_limit"] is None
    assert (await db.get_leaderboard_slots(1001))[0]["png"] == b"png-a"
    assert (await db.get_leaderboard_slots(1002))[0]["png"] == b"png-b"

    await db.set_leaderboard_limit(1001, None, 3012)
    assert (await db.get_guild_config(1001))["leaderboard_limit"] is None


@pytest.mark.asyncio
async def test_current_leaderboard_season_uses_latest_update_and_mmr_order(
    postgres_db: None,
) -> None:
    users = [
        User.from_dict(user_payload(discord_id=None)),
        User.from_dict(
            user_payload(
                id=USER_B_ID,
                discord_username="PlayerTwo",
                discord_id=None,
                member_number=8,
            )
        ),
    ]
    await db.sync_users_to_db(users)
    now = datetime.now(timezone.utc)
    older = SeasonRating.from_dict(
        season_rating_payload(
            id=501,
            user_id=USER_A_ID,
            season_number=1,
            mmr=9000,
            updated_at=(now - timedelta(days=1)).isoformat(),
        )
    )
    current_a = SeasonRating.from_dict(
        season_rating_payload(
            id=502,
            user_id=USER_A_ID,
            season_number=2,
            mmr=1500,
            updated_at=now.isoformat(),
        )
    )
    current_b = SeasonRating.from_dict(
        season_rating_payload(
            id=503,
            user_id=USER_B_ID,
            season_number=2,
            mmr=2500,
            updated_at=(now - timedelta(minutes=1)).isoformat(),
        )
    )
    await db.sync_season_ratings_to_db([older, current_a, current_b])

    season, rows = await db.get_current_leaderboard_ratings()

    assert season == (current_a.season_year, 2)
    assert [row["mmr"] for row in rows] == [2500, 1500]


@pytest.mark.asyncio
async def test_leaderboard_triggers_notify_only_for_relevant_actual_changes(
    postgres_db: None,
) -> None:
    dsn = get_test_database_url()
    listener = await asyncpg.connect(dsn=dsn)
    notifications: asyncio.Queue[tuple[str, str]] = asyncio.Queue()

    def callback(
        connection: asyncpg.Connection,
        pid: int,
        channel: str,
        payload: str,
    ) -> None:
        notifications.put_nowait((channel, payload))

    await listener.add_listener("leaderboard_changed", callback)
    try:
        user = User.from_dict(user_payload(discord_id=None))
        await db.sync_users_to_db([user])
        rating = SeasonRating.from_dict(season_rating_payload(user_id=USER_A_ID))
        await db.sync_season_ratings_to_db([rating])
        assert (await asyncio.wait_for(notifications.get(), 1))[0] == "leaderboard_changed"

        pool = db.get_pool()
        await pool.execute("UPDATE season_ratings SET mmr = mmr WHERE id = $1", rating.id)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(notifications.get(), 0.1)

        await pool.execute(
            "UPDATE users SET discord_username = $1 WHERE id = $2",
            "Renamed",
            uuid.UUID(USER_A_ID),
        )
        assert (await asyncio.wait_for(notifications.get(), 1))[0] == "leaderboard_changed"

        await pool.execute("DELETE FROM season_ratings WHERE id = $1", rating.id)
        assert (await asyncio.wait_for(notifications.get(), 1))[0] == "leaderboard_changed"
    finally:
        await listener.remove_listener("leaderboard_changed", callback)
        await listener.close()

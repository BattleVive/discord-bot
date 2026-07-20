from __future__ import annotations

from collections.abc import AsyncIterator
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
TABLES = {"users", "season_ratings", "lobbies", "lobby_rosters"}


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
    await conn.execute("DROP TABLE IF EXISTS lobby_rosters, season_ratings, lobbies, users CASCADE")
    for sql_file in sorted(SQL_DIR.glob("*.sql")):
        await conn.execute(sql_file.read_text(encoding="utf-8"))


async def drop_database_tables(conn: asyncpg.Connection) -> None:
    await conn.execute("DROP TABLE IF EXISTS lobby_rosters, season_ratings, lobbies, users CASCADE")


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

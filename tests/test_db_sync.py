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
from battlevive_bot.models import LobbyCaptain
from battlevive_bot.models import LobbyDraftAction
from battlevive_bot.models import SeasonRating
from battlevive_bot.models import User
from tests.factories import USER_A_ID
from tests.factories import USER_B_ID
from tests.factories import lobby_payload
from tests.factories import lobby_draft_action_payload
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
    "active_lobby_posts",
    "active_lobby_empty_posts",
    "active_lobby_obsolete_posts",
    "user_discord_links",
    "guild_command_channels",
    "guild_created_roles",
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
        "DROP TABLE IF EXISTS active_lobby_obsolete_posts, "
        "active_lobby_empty_posts, active_lobby_posts, leaderboard_slots, "
        "guild_command_channels, guild_created_roles, user_discord_links, "
        "guild_config, lobby_rosters, season_ratings, lobbies, users CASCADE"
    )
    for sql_file in sorted(SQL_DIR.glob("*.sql")):
        await conn.execute(sql_file.read_text(encoding="utf-8"))


async def drop_database_tables(conn: asyncpg.Connection) -> None:
    await conn.execute(
        "DROP TABLE IF EXISTS active_lobby_obsolete_posts, "
        "active_lobby_empty_posts, active_lobby_posts, leaderboard_slots, "
        "guild_command_channels, guild_created_roles, user_discord_links, "
        "guild_config, lobby_rosters, season_ratings, lobbies, users CASCADE"
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
    assert await pool.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'guild_config'
              AND column_name = 'website_moderator_role_id'
        )
        """
    )
    assert await pool.fetchval(
        """
        SELECT column_default = 'false'
        FROM information_schema.columns
        WHERE table_name = 'active_lobby_posts'
          AND column_name = 'dispute_notification_handled'
          AND is_nullable = 'NO'
        """
    )
    assert await pool.fetchval(
        """
        SELECT column_default = 'false'
        FROM information_schema.columns
        WHERE table_name = 'guild_config'
          AND column_name = 'debug_commands_enabled'
        """
    )
    assert await pool.fetchval(
        """
        SELECT column_default = 'true'
        FROM information_schema.columns
        WHERE table_name = 'guild_config'
          AND column_name = 'active_lobby_baseline_pending'
          AND is_nullable = 'NO'
        """
    )
    assert await pool.fetchval(
        "SELECT column_default = '20' FROM information_schema.columns WHERE table_name = 'guild_config' AND column_name = 'rank_cooldown_seconds'"
    )
    assert await pool.fetchval(
        "SELECT to_regclass('public.user_discord_links') IS NOT NULL"
    )
    assert not await pool.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'leaderboard_slots' AND column_name = 'png'
        )
        """
    )
    assert await pool.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'lobby_rosters' AND column_name = 'roster'
        )
        """
    )
    assert await pool.fetchval(
        """
        SELECT EXISTS (
            SELECT 1
            FROM pg_indexes
            WHERE tablename = 'active_lobby_obsolete_posts'
              AND indexname = 'idx_active_lobby_obsolete_posts_created'
        )
        """
    )
    assert not await pool.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'lobby_rosters' AND column_name = 'user_id'
        )
        """
    )
    assert await pool.fetchval(
        """
        SELECT EXISTS (
            SELECT 1
            FROM pg_indexes
            WHERE tablename = 'lobby_rosters'
              AND indexname = 'idx_lobby_rosters_roster'
              AND indexdef LIKE '%USING gin%'
        )
        """
    )
    constraint = await pool.fetchval(
        """
        SELECT pg_get_constraintdef(oid)
        FROM pg_constraint
        WHERE conrelid = 'guild_config'::regclass
          AND conname = 'guild_config_leaderboard_limit_range'
        """
    )
    assert constraint is not None
    assert "50" in constraint
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
    obsolete_trigger_names = {
        row["trigger_name"]
        for row in await pool.fetch(
            """
            SELECT trigger_name
            FROM information_schema.triggers
            WHERE event_object_table = 'active_lobby_obsolete_posts'
            """
        )
    }
    assert {
        "active_lobby_obsolete_posts_insert",
        "active_lobby_obsolete_posts_update",
        "active_lobby_obsolete_posts_delete",
    }.issubset(obsolete_trigger_names)


@pytest.mark.asyncio
async def test_active_lobby_migration_preserves_legacy_rosters_without_captains(
    postgres_db: None,
) -> None:
    pool = db.get_pool()
    await drop_database_tables(pool)
    for sql_file in sorted(SQL_DIR.glob("*.sql")):
        if sql_file.name == "08_active_lobbies.sql":
            break
        await pool.execute(sql_file.read_text(encoding="utf-8"))

    await pool.execute(
        """
        ALTER TABLE guild_config
            ADD COLUMN active_lobby_channel_id BIGINT,
            ADD COLUMN active_lobby_role_id BIGINT
        """
    )
    await pool.execute(
        """
        INSERT INTO guild_config (
            guild_id, active_lobby_channel_id, updated_by
        )
        VALUES (1001, 2001, 3001)
        """
    )

    await pool.execute(
        """
        INSERT INTO users (
            id, discord_username, member_number, tournaments_joined
        )
        VALUES
            ($1, 'PlayerOne', 1, 0),
            ($2, 'PlayerTwo', 2, 0)
        """,
        uuid.UUID(USER_A_ID),
        uuid.UUID(USER_B_ID),
    )
    await pool.execute(
        """
        INSERT INTO lobbies (
            id, lobby_number, title, lobby_type, region, match_size,
            team_one_name, team_two_name, status, season_year,
            season_number, created_at
        )
        VALUES (
            101, 44, 'Legacy lobby', 'ranked', 'EU', 2,
            'Blue', 'Red', 'active', 2026, 1, now()
        )
        """
    )
    await pool.executemany(
        """
        INSERT INTO lobby_rosters (lobby_id, user_id, team)
        VALUES ($1, $2, $3)
        """,
        [
            (101, uuid.UUID(USER_B_ID), "team_one"),
            (101, uuid.UUID(USER_A_ID), "team_one"),
        ],
    )

    migration = (SQL_DIR / "08_active_lobbies.sql").read_text(encoding="utf-8")
    await pool.execute(migration)
    await pool.execute(migration)
    migration_09 = (SQL_DIR / "09_command_controls_and_identity_recovery.sql").read_text(
        encoding="utf-8"
    )
    await pool.execute(migration_09)
    await pool.execute(migration_09)

    roster = await pool.fetchrow(
        """
        SELECT captain_id, roster::TEXT[], picks, bans, draft_finalized_at
        FROM lobby_rosters
        WHERE lobby_id = 101 AND team = 'team_one'
        """
    )
    assert roster["captain_id"] is None
    assert roster["roster"] == [USER_A_ID, USER_B_ID]
    assert roster["picks"] == []
    assert roster["bans"] == []
    assert roster["draft_finalized_at"] is None
    upgraded_config = await db.get_guild_config(1001)
    assert upgraded_config is not None
    assert upgraded_config["active_lobby_channel_id"] == 2001
    assert upgraded_config["website_moderator_role_id"] is None
    assert upgraded_config["active_lobby_baseline_pending"] is True
    assert await pool.fetchval(
        """
        SELECT dispute_notification_handled = FALSE
        FROM active_lobby_posts
        LIMIT 1
        """
    ) is None
    assert await pool.fetchval(
        "SELECT to_regclass('public.active_lobby_obsolete_posts') IS NOT NULL"
    )


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
        FROM lobby_rosters, unnest(roster) AS roster_user(user_id)
        WHERE lobby_id = $1
        ORDER BY team, roster_user.user_id
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
async def test_authoritative_delete_retains_link_and_same_uuid_rehydrates(
    postgres_db: None,
) -> None:
    user = User.from_dict(user_payload(discord_id=None))
    await db.sync_users_to_db([user])
    assert await db.set_user_discord_id(USER_A_ID, 111111111111111111)

    deleted = await db.sync_users_to_db([])
    assert deleted == [uuid.UUID(USER_A_ID)]
    assert await db.get_active_user_by_discord_id(111111111111111111) is None
    assert await db.get_pool().fetchval(
        "SELECT discord_id FROM user_discord_links WHERE user_id = $1",
        uuid.UUID(USER_A_ID),
    ) == 111111111111111111

    await db.sync_users_to_db([user])
    restored = await db.get_active_user_by_discord_id(111111111111111111)
    assert restored is not None
    assert restored["id"] == uuid.UUID(USER_A_ID)
    assert restored["discord_id"] == 111111111111111111


@pytest.mark.asyncio
async def test_migration_09_copies_existing_links_and_is_idempotent(
    postgres_db: None,
) -> None:
    await db.get_pool().execute(
        """
        INSERT INTO users (
            id, discord_username, discord_id, member_number, tournaments_joined
        )
        VALUES ($1, 'PlayerOne', $2, 7, 0)
        """,
        uuid.UUID(USER_A_ID),
        111111111111111111,
    )
    migration = (
        SQL_DIR / "09_command_controls_and_identity_recovery.sql"
    ).read_text(encoding="utf-8")
    await db.get_pool().execute(migration)
    await db.get_pool().execute(migration)
    assert await db.get_pool().fetchval(
        "SELECT discord_id FROM user_discord_links WHERE user_id = $1",
        uuid.UUID(USER_A_ID),
    ) == 111111111111111111


@pytest.mark.asyncio
async def test_different_uuid_can_reclaim_only_an_inactive_recovery_link(
    postgres_db: None,
) -> None:
    old_user = User.from_dict(user_payload(discord_id=None))
    new_user = User.from_dict(
        user_payload(id=USER_B_ID, discord_id=None, member_number=8)
    )
    await db.sync_users_to_db([old_user, new_user])
    assert await db.set_user_discord_id(USER_A_ID, 111111111111111111)
    assert not await db.set_user_discord_id(USER_B_ID, 111111111111111111)

    await db.sync_users_to_db([new_user])
    assert await db.set_user_discord_id(USER_B_ID, 111111111111111111)
    assert await db.get_pool().fetchval(
        "SELECT user_id FROM user_discord_links WHERE discord_id = $1",
        111111111111111111,
    ) == uuid.UUID(USER_B_ID)


@pytest.mark.asyncio
async def test_concurrent_linking_cannot_duplicate_or_steal_active_link(
    postgres_db: None,
) -> None:
    user_a = User.from_dict(user_payload(discord_id=None))
    user_b = User.from_dict(
        user_payload(id=USER_B_ID, discord_id=None, member_number=8)
    )
    await db.sync_users_to_db([user_a, user_b])
    results = await asyncio.gather(
        db.set_user_discord_id(USER_A_ID, 111111111111111111),
        db.set_user_discord_id(USER_B_ID, 111111111111111111),
    )
    assert sorted(results) == [False, True]
    assert await db.get_pool().fetchval(
        "SELECT count(*) FROM user_discord_links WHERE discord_id = $1",
        111111111111111111,
    ) == 1


@pytest.mark.asyncio
async def test_command_controls_and_generated_role_ownership_round_trip(
    postgres_db: None,
) -> None:
    await db.set_rank_cooldown_seconds(1001, 0, 3001)
    assert (await db.get_guild_config(1001))["rank_cooldown_seconds"] == 0
    await db.set_rank_cooldown_seconds(1001, 3600, 3001)
    assert (await db.get_guild_config(1001))["rank_cooldown_seconds"] == 3600
    with pytest.raises(ValueError):
        await db.set_rank_cooldown_seconds(1001, -1, 3001)
    with pytest.raises(ValueError):
        await db.set_rank_cooldown_seconds(1001, 3601, 3001)

    await db.set_command_channel_rule(1001, 2001, "allow", 3001)
    await db.set_command_channel_rule(1001, 2001, "block", 3002)
    assert [rule["rule"] for rule in await db.get_command_channel_rules(1001)] == [
        "block"
    ]
    assert await db.remove_command_channel_rule(1001, 2001)
    assert not await db.remove_command_channel_rule(1001, 2001)

    await db.record_created_role(1001, "active_lobby", 4001, 3001)
    assert (await db.get_created_role(1001, "active_lobby"))["role_id"] == 4001
    assert await db.forget_created_role(1001, "active_lobby", 4001)


@pytest.mark.asyncio
async def test_authoritative_delete_cascades_and_scrubs_dependent_records(
    postgres_db: None,
) -> None:
    user_a = User.from_dict(user_payload(discord_id=None))
    user_b = User.from_dict(
        user_payload(id=USER_B_ID, discord_id=None, member_number=8)
    )
    lobby = Lobby.from_dict(
        lobby_payload(
            creator_id=USER_A_ID,
            team_one_roster=[USER_A_ID, USER_B_ID],
            team_two_roster=[],
        )
    )
    rating = SeasonRating.from_dict(season_rating_payload(user_id=USER_A_ID))
    await db.sync_battlevive_data_to_db([user_a, user_b], [lobby], [rating])

    await db.sync_users_to_db([user_b])
    pool = db.get_pool()
    assert await pool.fetchval("SELECT count(*) FROM season_ratings") == 0
    assert await pool.fetchval("SELECT creator_id FROM lobbies WHERE id = $1", lobby.id) is None
    assert await pool.fetchval(
        "SELECT roster::TEXT[] FROM lobby_rosters WHERE lobby_id = $1 AND team = 'team_one'",
        lobby.id,
    ) == [USER_B_ID]


@pytest.mark.asyncio
async def test_active_lobby_candidates_include_ordered_teams_and_local_users(
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
            status="active",
            team_one_roster=[USER_B_ID, USER_A_ID],
            team_two_roster=[],
        )
    )
    await db.sync_battlevive_data_to_db([user_a, user_b], [lobby])
    await db.set_user_discord_id(USER_A_ID, 111111111111111111)
    await db.update_lobby_captains(
        lobby.id,
        [LobbyCaptain(user_id=USER_B_ID, slot="team_one")],
    )

    candidates = await db.get_active_lobby_candidates()

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["team_one_roster"] == [USER_B_ID, USER_A_ID]
    assert candidate["team_two_roster"] == []
    assert candidate["team_one_captain_id"] == USER_B_ID
    assert candidate["team_two_captain_id"] is None
    assert candidate["team_one_picks"] == []
    assert candidate["draft_finalized_at"] is None
    assert candidate["users_by_id"] == {
        USER_A_ID: {
            "id": USER_A_ID,
            "discord_id": 111111111111111111,
            "discord_username": "PlayerOne",
        },
        USER_B_ID: {
            "id": USER_B_ID,
            "discord_id": None,
            "discord_username": "PlayerTwo",
        },
    }
    assert await db.get_lobby_ids_for_user(USER_A_ID) == [lobby.id]
    assert await db.get_lobby_ids_for_user(USER_B_ID) == [lobby.id]


@pytest.mark.asyncio
async def test_refresh_preserves_captains_and_final_draft_state(
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
    lobby = Lobby.from_dict(
        lobby_payload(
            status="active",
            team_one_roster=[USER_A_ID],
            team_two_roster=[USER_B_ID],
        )
    )
    await db.sync_battlevive_data_to_db(users, [lobby])
    await db.update_lobby_captains(
        lobby.id,
        [
            LobbyCaptain(user_id=USER_A_ID, slot="team_one"),
            LobbyCaptain(user_id=USER_B_ID, slot="team_two"),
        ],
    )
    finalized_at = datetime(2026, 1, 2, 19, 0, tzinfo=timezone.utc)
    actions = [
        LobbyDraftAction.from_dict(
            lobby_draft_action_payload(
                id=904,
                step=4,
                team_slot="team_one",
                action="pick",
                champion="Freya",
            )
        ),
        LobbyDraftAction.from_dict(
            lobby_draft_action_payload(
                id=901,
                step=1,
                team_slot="team_one",
                action="pick",
                champion="Lucie",
            )
        ),
        LobbyDraftAction.from_dict(
            lobby_draft_action_payload(
                id=902,
                step=2,
                team_slot="team_two",
                action="ban",
                champion="Oldur",
            )
        ),
        LobbyDraftAction.from_dict(
            lobby_draft_action_payload(
                id=903,
                step=3,
                team_slot="team_one",
                action="map_ban",
                champion="Dragon Garden",
            )
        ),
    ]
    await db.finalize_lobby_draft(lobby.id, actions, finalized_at)

    refreshed = Lobby.from_dict(
        lobby_payload(
            status="awaiting_votes",
            ended_at="2026-01-02T18:55:00+00:00",
            team_one_roster=[USER_B_ID, USER_A_ID],
            team_two_roster=[],
        )
    )
    await db.sync_lobbies_to_db([refreshed])

    candidate = (await db.get_active_lobby_candidates())[0]
    assert candidate["team_one_roster"] == [USER_B_ID, USER_A_ID]
    assert candidate["team_one_captain_id"] == USER_A_ID
    assert candidate["team_two_captain_id"] == USER_B_ID
    assert candidate["team_one_picks"] == ["Lucie", "Freya"]
    assert candidate["team_two_bans"] == ["Oldur"]
    assert candidate["team_one_bans"] == []
    assert candidate["draft_finalized_at"] == finalized_at


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
    await db.set_debug_commands_enabled(1001, True, 3012)

    assert await db.get_guild_config(1001) == {
        "guild_id": 1001,
        "leaderboard_channel_id": 2011,
        "leaderboard_limit": None,
        "debug_commands_enabled": True,
        "active_lobby_channel_id": None,
        "active_lobby_role_id": None,
        "website_moderator_role_id": None,
        "active_lobby_baseline_pending": True,
        "rank_cooldown_seconds": 20,
        "guide_forum_channel_id": None,
        "guide_notification_role_id": None,
        "guide_auto_delete_on_removal": False,
        "updated_by": 3012,
    }
    assert await db.get_guild_config(1002) == {
        "guild_id": 1002,
        "leaderboard_channel_id": 2002,
        "leaderboard_limit": None,
        "debug_commands_enabled": False,
        "active_lobby_channel_id": None,
        "active_lobby_role_id": None,
        "website_moderator_role_id": None,
        "active_lobby_baseline_pending": True,
        "rank_cooldown_seconds": 20,
        "guide_forum_channel_id": None,
        "guide_notification_role_id": None,
        "guide_auto_delete_on_removal": False,
        "updated_by": 3002,
    }

    await db.reset_guild_config(1001, 3021)

    assert await db.get_guild_config(1001) == {
        "guild_id": 1001,
        "leaderboard_channel_id": None,
        "leaderboard_limit": None,
        "debug_commands_enabled": True,
        "active_lobby_channel_id": None,
        "active_lobby_role_id": None,
        "website_moderator_role_id": None,
        "active_lobby_baseline_pending": True,
        "rank_cooldown_seconds": 20,
        "guide_forum_channel_id": None,
        "guide_notification_role_id": None,
        "guide_auto_delete_on_removal": False,
        "updated_by": 3021,
    }


@pytest.mark.asyncio
async def test_active_lobby_baseline_is_durable_across_moves_and_reset(
    postgres_db: None,
) -> None:
    await db.set_active_lobby_channel(1001, 2001, 3001)
    configured = await db.get_guild_config(1001)
    assert configured is not None
    assert configured["active_lobby_baseline_pending"] is True

    await db.complete_active_lobby_baseline(1001)
    completed = await db.get_guild_config(1001)
    assert completed is not None
    assert completed["active_lobby_baseline_pending"] is False

    await db.set_active_lobby_channel(1001, 2002, 3002)
    moved = await db.get_guild_config(1001)
    assert moved is not None
    assert moved["active_lobby_channel_id"] == 2002
    assert moved["active_lobby_baseline_pending"] is False

    await db.reset_active_lobby_config(1001, 3003)
    await db.complete_active_lobby_baseline(1001)
    disabled = await db.get_guild_config(1001)
    assert disabled is not None
    assert disabled["active_lobby_channel_id"] is None
    assert disabled["active_lobby_baseline_pending"] is True

    user = User.from_dict(user_payload(discord_id=None))
    lobby = Lobby.from_dict(
        lobby_payload(
            status="active",
            team_one_roster=[USER_A_ID],
            team_two_roster=[],
        )
    )
    await db.sync_battlevive_data_to_db([user], [lobby])

    await db.set_active_lobby_channel(1001, 2003, 3004)
    reconfigured = (await db.get_configured_active_lobbies())[0]
    assert reconfigured["active_lobby_channel_id"] == 2003
    assert reconfigured["active_lobby_baseline_pending"] is True

    await db.complete_active_lobby_baseline(1001)
    final = await db.get_guild_config(1001)
    assert final is not None
    assert final["active_lobby_baseline_pending"] is False


@pytest.mark.asyncio
async def test_pending_baseline_upgrades_existing_notification_state_monotonically(
    postgres_db: None,
) -> None:
    user = User.from_dict(user_payload(discord_id=None))
    lobby = Lobby.from_dict(
        lobby_payload(
            status="active",
            team_one_roster=[USER_A_ID],
            team_two_roster=[],
        )
    )
    await db.sync_battlevive_data_to_db([user], [lobby])
    await db.set_active_lobby_channel(1001, 2001, 3001)
    config = await db.get_guild_config(1001)
    assert config is not None
    assert config["active_lobby_baseline_pending"] is True

    await db.ensure_active_lobby_post_states(
        1001,
        [lobby.id],
        notification_handled=False,
        dispute_notification_handled=False,
    )
    original = (await db.get_active_lobby_post_states(1001))[0]
    assert original["notification_handled"] is False

    await db.ensure_active_lobby_post_states(
        1001,
        [lobby.id],
        notification_handled=True,
        dispute_notification_handled=True,
    )
    upgraded = (await db.get_active_lobby_post_states(1001))[0]
    assert upgraded["notification_handled"] is True
    assert upgraded["dispute_notification_handled"] is True
    assert upgraded["first_seen_at"] == original["first_seen_at"]
    assert upgraded["updated_at"] >= original["updated_at"]

    await db.ensure_active_lobby_post_states(
        1001,
        [lobby.id],
        notification_handled=False,
        dispute_notification_handled=False,
    )
    retained = (await db.get_active_lobby_post_states(1001))[0]
    assert retained["notification_handled"] is True
    assert retained["first_seen_at"] == original["first_seen_at"]
    assert retained["updated_at"] == upgraded["updated_at"]


@pytest.mark.asyncio
async def test_obsolete_active_lobby_posts_are_idempotent_and_guild_isolated(
    postgres_db: None,
) -> None:
    user = User.from_dict(user_payload(discord_id=None))
    lobby = Lobby.from_dict(
        lobby_payload(
            status="active",
            team_one_roster=[USER_A_ID],
            team_two_roster=[],
        )
    )
    await db.sync_battlevive_data_to_db([user], [lobby])
    await db.set_active_lobby_channel(1001, 2001, 3001)
    await db.set_active_lobby_channel(1002, 2002, 3002)

    await db.record_active_lobby_obsolete_post(1001, 2000, 5001, lobby.id)
    await db.record_active_lobby_obsolete_post(1001, 2000, 5001, None)
    await db.record_active_lobby_obsolete_post(1001, 2000, 5002, None)
    await db.record_active_lobby_obsolete_post(1002, 2000, 5001, lobby.id)

    first_guild = await db.get_active_lobby_obsolete_posts(1001)
    assert len(first_guild) == 2
    assert [row["lobby_id"] for row in first_guild] == [lobby.id, None]
    assert all(row["guild_id"] == 1001 for row in first_guild)
    assert len(await db.get_active_lobby_obsolete_posts(1002)) == 1

    await db.reset_active_lobby_config(1001, 3003)
    configured = await db.get_configured_active_lobbies()
    reset_guild = next(row for row in configured if row["guild_id"] == 1001)
    assert reset_guild["active_lobby_channel_id"] is None
    assert reset_guild["active_lobby_baseline_pending"] is True

    assert not await db.delete_active_lobby_obsolete_post(1001, 2000, 9999)
    assert await db.delete_active_lobby_obsolete_post(1001, 2000, 5001)
    assert [
        row["message_id"]
        for row in await db.get_active_lobby_obsolete_posts(1001)
    ] == [5002]
    assert len(await db.get_active_lobby_obsolete_posts(1002)) == 1

    await db.get_pool().execute("DELETE FROM lobbies WHERE id = $1", lobby.id)
    second_guild = await db.get_active_lobby_obsolete_posts(1002)
    assert second_guild[0]["lobby_id"] is None


@pytest.mark.asyncio
async def test_authoritative_lobby_sync_queues_posts_before_deleting_missing_rows(
    postgres_db: None,
) -> None:
    user = User.from_dict(user_payload(discord_id=None))
    kept = Lobby.from_dict(
        lobby_payload(
            id=101,
            lobby_number=101,
            status="active",
            team_one_roster=[USER_A_ID],
            team_two_roster=[],
        )
    )
    removed = Lobby.from_dict(
        lobby_payload(
            id=102,
            lobby_number=102,
            status="open",
            team_one_roster=[USER_A_ID],
            team_two_roster=[],
        )
    )
    await db.sync_battlevive_data_to_db([user], [kept, removed])
    await db.set_active_lobby_channel(1001, 2001, 3001)
    await db.ensure_active_lobby_post_states(
        1001,
        [removed.id],
        notification_handled=True,
        dispute_notification_handled=False,
    )
    await db.record_active_lobby_post(
        1001,
        removed.id,
        2001,
        5001,
        "removed",
        notification_handled=True,
        dispute_notification_handled=False,
    )

    await db.sync_lobbies_to_db([kept])

    assert [lobby.id for lobby in await db.get_lobbies()] == [kept.id]
    assert await db.get_active_lobby_post_states(1001) == []
    obsolete = await db.get_active_lobby_obsolete_posts(1001)
    assert [(row["lobby_id"], row["message_id"]) for row in obsolete] == [
        (None, 5001)
    ]
    assert not await db.get_pool().fetchval(
        "SELECT EXISTS (SELECT 1 FROM lobby_rosters WHERE lobby_id = $1)",
        removed.id,
    )

    await db.sync_lobbies_to_db([])
    assert await db.get_lobbies() == []


@pytest.mark.asyncio
async def test_active_lobby_config_and_post_history_survive_reset_for_cleanup(
    postgres_db: None,
) -> None:
    user_a = User.from_dict(user_payload(discord_id=None))
    lobby = Lobby.from_dict(
        lobby_payload(
            status="active",
            team_one_roster=[USER_A_ID],
            team_two_roster=[],
        )
    )
    await db.sync_battlevive_data_to_db([user_a], [lobby])
    await db.set_active_lobby_channel(1001, 2001, 3001)
    await db.set_active_lobby_role(1001, 4001, 3002)
    await db.set_website_moderator_role(1001, 4002, 3002)
    await db.ensure_active_lobby_post_states(
        1001,
        [lobby.id],
        notification_handled=True,
        dispute_notification_handled=True,
    )
    first_seen = (await db.get_active_lobby_post_states(1001))[0]["first_seen_at"]
    await db.ensure_active_lobby_post_states(
        1001,
        [lobby.id],
        notification_handled=False,
        dispute_notification_handled=False,
    )
    await db.record_active_lobby_post(
        1001,
        lobby.id,
        2001,
        5001,
        "lobby-fingerprint",
        notification_handled=False,
        dispute_notification_handled=False,
    )
    await db.record_active_lobby_empty_post(
        1001,
        2001,
        5002,
        "empty-fingerprint",
    )
    await db.get_pool().execute(
        """
        UPDATE lobbies
        SET status = 'finished', ended_at = now(), winner_slot = 'team_one'
        WHERE id = $1
        """,
        lobby.id,
    )

    config = await db.get_guild_config(1001)
    assert config["active_lobby_channel_id"] == 2001
    assert config["active_lobby_role_id"] == 4001
    assert config["website_moderator_role_id"] == 4002
    state = (await db.get_active_lobby_post_states(1001))[0]
    assert state["first_seen_at"] == first_seen
    assert state["notification_handled"] is True
    assert [candidate["id"] for candidate in await db.get_active_lobby_candidates()] == [
        lobby.id
    ]

    await db.reset_active_lobby_config(1001, 3003)

    reset_config = await db.get_guild_config(1001)
    assert reset_config["active_lobby_channel_id"] is None
    assert reset_config["active_lobby_role_id"] is None
    assert reset_config["website_moderator_role_id"] is None
    assert await db.get_configured_active_lobbies() == [
        {
            "guild_id": 1001,
            "active_lobby_channel_id": None,
            "active_lobby_role_id": None,
            "website_moderator_role_id": None,
            "active_lobby_baseline_pending": True,
            "updated_by": 3003,
        }
    ]
    retained = (await db.get_active_lobby_post_states(1001))[0]
    assert retained["message_id"] == 5001
    assert retained["notification_handled"] is True

    assert not await db.clear_active_lobby_post_message(
        1001,
        lobby.id,
        2001,
        9999,
    )
    assert await db.clear_active_lobby_post_message(
        1001,
        lobby.id,
        2001,
        5001,
    )
    assert await db.clear_active_lobby_empty_post(1001, 2001, 5002)
    assert await db.get_configured_active_lobbies() == []

    cleared = (await db.get_active_lobby_post_states(1001))[0]
    assert cleared["message_id"] is None
    assert cleared["notification_handled"] is True
    empty = await db.get_active_lobby_empty_post(1001)
    assert empty is not None
    assert empty["message_id"] is None


@pytest.mark.asyncio
async def test_reset_preserves_cached_leaderboard_metadata_for_later_reuse(
    postgres_db: None,
) -> None:
    await db.upsert_guild_config(1001, 2001, 3001)
    await db.replace_leaderboard_slots(
        1001,
        [
            {
                "slot": 0,
                "channel_id": 2001,
                "message_id": 4001,
                "season_year": 2026,
                "season_number": 7,
                "user_id": None,
                "fingerprint": "header-a",
            }
        ],
    )

    await db.reset_guild_config(1001, 3021)

    stored = await db.get_leaderboard_slots(1001)
    assert stored[0]["fingerprint"] == "header-a"
    assert stored[0]["channel_id"] is None
    assert stored[0]["message_id"] is None


@pytest.mark.asyncio
async def test_leaderboard_limit_and_slot_metadata_are_isolated_by_guild(
    postgres_db: None,
) -> None:
    await db.upsert_guild_config(1001, 2001, 3001)
    await db.upsert_guild_config(1002, 2002, 3002)
    await db.set_leaderboard_limit(1001, 5, 3011)
    await db.replace_leaderboard_slots(
        1001,
        [
            {
                "slot": 0,
                "channel_id": 2001,
                "message_id": 4001,
                "season_year": 2026,
                "season_number": 7,
                "user_id": None,
                "fingerprint": "header-a",
            }
        ],
    )
    await db.replace_leaderboard_slots(
        1002,
        [
            {
                "slot": 0,
                "channel_id": 2002,
                "message_id": 4002,
                "season_year": 2026,
                "season_number": 7,
                "user_id": None,
                "fingerprint": "header-b",
            }
        ],
    )

    assert (await db.get_guild_config(1001))["leaderboard_limit"] == 5
    assert (await db.get_guild_config(1002))["leaderboard_limit"] is None
    assert (await db.get_leaderboard_slots(1001))[0]["fingerprint"] == "header-a"
    assert (await db.get_leaderboard_slots(1002))[0]["fingerprint"] == "header-b"

    await db.set_leaderboard_limit(1001, None, 3012)
    assert (await db.get_guild_config(1001))["leaderboard_limit"] is None


@pytest.mark.asyncio
async def test_replacing_slot_metadata_is_transactional_and_removes_stale_slots(
    postgres_db: None,
) -> None:
    await db.upsert_guild_config(1001, 2001, 3001)
    original = [
        {
            "slot": 0,
            "channel_id": 2001,
            "message_id": 4001,
            "season_year": 2026,
            "season_number": 7,
            "user_id": None,
            "fingerprint": "header-a",
        },
        {
            "slot": 1,
            "channel_id": None,
            "message_id": None,
            "season_year": 2026,
            "season_number": 7,
            "user_id": USER_A_ID,
            "fingerprint": "entry-a",
        },
    ]
    await db.replace_leaderboard_slots(1001, original)

    await db.replace_leaderboard_slots(
        1001,
        [
            {
                **original[0],
                "fingerprint": "header-b",
            }
        ],
    )

    stored = await db.get_leaderboard_slots(1001)
    assert [(row["slot"], row["fingerprint"]) for row in stored] == [(0, "header-b")]


@pytest.mark.asyncio
async def test_leaderboard_limit_is_constrained_to_one_through_fifty(
    postgres_db: None,
) -> None:
    await db.upsert_guild_config(1001, 2001, 3001)
    await db.set_leaderboard_limit(1001, 50, 3001)

    assert (await db.get_guild_config(1001))["leaderboard_limit"] == 50
    with pytest.raises(ValueError, match="50"):
        await db.set_leaderboard_limit(1001, 51, 3001)


@pytest.mark.asyncio
async def test_disk_cache_migration_clamps_existing_limits_before_adding_range_constraint(
    postgres_db: None,
) -> None:
    pool = db.get_pool()
    await db.upsert_guild_config(1001, 2001, 3001)
    await pool.execute(
        "ALTER TABLE guild_config DROP CONSTRAINT guild_config_leaderboard_limit_range"
    )
    await pool.execute("UPDATE guild_config SET leaderboard_limit = 99 WHERE guild_id = 1001")

    await pool.execute(
        (SQL_DIR / "06_leaderboard_disk_cache.sql").read_text(encoding="utf-8")
    )

    assert (await db.get_guild_config(1001))["leaderboard_limit"] == 50


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


@pytest.mark.asyncio
async def test_active_lobby_triggers_notify_for_relevant_changes(
    postgres_db: None,
) -> None:
    user = User.from_dict(user_payload(discord_id=None))
    lobby = Lobby.from_dict(
        lobby_payload(
            status="active",
            team_one_roster=[USER_A_ID],
            team_two_roster=[],
        )
    )
    await db.sync_battlevive_data_to_db([user], [lobby])
    await db.upsert_guild_config(1001, None, 3001)

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

    await listener.add_listener("active_lobby_changed", callback)
    pool = db.get_pool()
    try:
        await pool.execute(
            "UPDATE lobbies SET draft_step = draft_step + 1 WHERE id = $1",
            lobby.id,
        )
        channel, payload = await asyncio.wait_for(notifications.get(), 1)
        assert channel == "active_lobby_changed"
        assert '"table" : "lobbies"' in payload

        await pool.execute(
            "UPDATE users SET discord_username = 'Renamed' WHERE id = $1",
            uuid.UUID(USER_A_ID),
        )
        assert '"table" : "users"' in (
            await asyncio.wait_for(notifications.get(), 1)
        )[1]

        await db.update_lobby_captains(
            lobby.id,
            [LobbyCaptain(user_id=USER_A_ID, slot="team_one")],
        )
        assert '"table" : "lobby_rosters"' in (
            await asyncio.wait_for(notifications.get(), 1)
        )[1]

        await db.set_active_lobby_channel(1001, 2001, 3002)
        assert '"table" : "guild_config"' in (
            await asyncio.wait_for(notifications.get(), 1)
        )[1]

        await db.record_active_lobby_obsolete_post(
            1001,
            1999,
            4999,
            lobby.id,
        )
        assert '"table" : "active_lobby_obsolete_posts"' in (
            await asyncio.wait_for(notifications.get(), 1)
        )[1]

        await db.delete_active_lobby_obsolete_post(1001, 1999, 4999)
        assert '"table" : "active_lobby_obsolete_posts"' in (
            await asyncio.wait_for(notifications.get(), 1)
        )[1]

        await pool.execute(
            "UPDATE lobbies SET draft_step = draft_step WHERE id = $1",
            lobby.id,
        )
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(notifications.get(), 0.1)
    finally:
        await listener.remove_listener("active_lobby_changed", callback)
        await listener.close()

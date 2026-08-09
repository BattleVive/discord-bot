from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from datetime import timezone
import uuid
from typing import Any

import asyncpg

from .logs import logger
from .models import Lobby
from .models import LobbyCaptain
from .models import LobbyDraftAction
from .models import SeasonRating
from .models import User
from .settings import LEADERBOARD_MAX_ENTRIES


_pool: asyncpg.Pool | None = None


class MissingUsersError(RuntimeError):
    """Raised when refreshed data references users not yet synced locally."""


async def init_pool(dsn: str | None) -> asyncpg.Pool:
    global _pool
    _pool = await asyncpg.create_pool(
        dsn=dsn,
        min_size=1,
        max_size=5,  # 2-5 guilds, single bot process - no need for a large pool.
        command_timeout=10,
    )
    logger.info("PostgreSQL connection pool created.")
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("PostgreSQL connection pool closed.")


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Connection pool not initialized. Call init_pool() first.")
    return _pool


async def get_guild_config(
    guild_id: int,
) -> dict[str, int | bool | None] | None:
    pool = get_pool()
    row = await pool.fetchrow(
        """
        SELECT guild_id, leaderboard_channel_id, leaderboard_limit,
               debug_commands_enabled, active_lobby_channel_id,
               active_lobby_role_id, website_moderator_role_id,
               active_lobby_baseline_pending,
               rank_cooldown_seconds,
               updated_by
        FROM guild_config
        WHERE guild_id = $1
        """,
        guild_id,
    )
    return dict(row) if row is not None else None


async def upsert_guild_config(
    guild_id: int,
    leaderboard_channel_id: int | None,
    updated_by: int,
) -> None:
    pool = get_pool()
    await pool.execute(
        """
        INSERT INTO guild_config (guild_id, leaderboard_channel_id, updated_by)
        VALUES ($1, $2, $3)
        ON CONFLICT (guild_id) DO UPDATE
        SET leaderboard_channel_id = EXCLUDED.leaderboard_channel_id,
            updated_at = now(),
            updated_by = EXCLUDED.updated_by
        """,
        guild_id,
        leaderboard_channel_id,
        updated_by,
    )


async def ensure_guild_config(guild_id: int, updated_by: int) -> None:
    await get_pool().execute(
        "INSERT INTO guild_config (guild_id, updated_by) VALUES ($1, $2) ON CONFLICT (guild_id) DO NOTHING",
        guild_id,
        updated_by,
    )


async def set_leaderboard_limit(
    guild_id: int,
    leaderboard_limit: int | None,
    updated_by: int,
) -> None:
    if leaderboard_limit is not None and not (
        1 <= leaderboard_limit <= LEADERBOARD_MAX_ENTRIES
    ):
        raise ValueError(
            f"leaderboard_limit must be between 1 and "
            f"{LEADERBOARD_MAX_ENTRIES}, or None"
        )

    pool = get_pool()
    await pool.execute(
        """
        INSERT INTO guild_config (guild_id, leaderboard_limit, updated_by)
        VALUES ($1, $2, $3)
        ON CONFLICT (guild_id) DO UPDATE
        SET leaderboard_limit = EXCLUDED.leaderboard_limit,
            updated_at = now(),
            updated_by = EXCLUDED.updated_by
        """,
        guild_id,
        leaderboard_limit,
        updated_by,
    )


async def set_debug_commands_enabled(
    guild_id: int,
    enabled: bool,
    updated_by: int,
) -> None:
    await get_pool().execute(
        """
        INSERT INTO guild_config (guild_id, debug_commands_enabled, updated_by)
        VALUES ($1, $2, $3)
        ON CONFLICT (guild_id) DO UPDATE
        SET debug_commands_enabled = EXCLUDED.debug_commands_enabled,
            updated_at = now(),
            updated_by = EXCLUDED.updated_by
        """,
        guild_id,
        enabled,
        updated_by,
    )


async def reset_guild_config(guild_id: int, updated_by: int) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO guild_config (
                    guild_id, leaderboard_channel_id, leaderboard_limit, updated_by
                )
                VALUES ($1, NULL, NULL, $2)
                ON CONFLICT (guild_id) DO UPDATE
                SET leaderboard_channel_id = NULL,
                    leaderboard_limit = NULL,
                    updated_at = now(),
                    updated_by = EXCLUDED.updated_by
                """,
                guild_id,
                updated_by,
            )
            await conn.execute(
                """
                UPDATE leaderboard_slots
                SET channel_id = NULL, message_id = NULL, updated_at = now()
                WHERE guild_id = $1
                """,
                guild_id,
            )


async def get_configured_leaderboards() -> list[dict[str, int | None]]:
    rows = await get_pool().fetch(
        """
        SELECT guild_id, leaderboard_channel_id, leaderboard_limit, updated_by
        FROM guild_config
        WHERE leaderboard_channel_id IS NOT NULL
        ORDER BY guild_id
        """
    )
    return [dict(row) for row in rows]


async def get_configured_active_lobbies() -> list[dict[str, int | bool | None]]:
    rows = await get_pool().fetch(
        """
        SELECT config.guild_id, config.active_lobby_channel_id,
               config.active_lobby_role_id,
               config.website_moderator_role_id,
               config.active_lobby_baseline_pending, config.updated_by
        FROM guild_config AS config
        WHERE config.active_lobby_channel_id IS NOT NULL
           OR EXISTS (
               SELECT 1
               FROM active_lobby_posts AS posts
               WHERE posts.guild_id = config.guild_id
                 AND posts.message_id IS NOT NULL
           )
           OR EXISTS (
               SELECT 1
               FROM active_lobby_empty_posts AS empty_posts
               WHERE empty_posts.guild_id = config.guild_id
                 AND empty_posts.message_id IS NOT NULL
           )
           OR EXISTS (
               SELECT 1
               FROM active_lobby_obsolete_posts AS obsolete_posts
               WHERE obsolete_posts.guild_id = config.guild_id
           )
        ORDER BY config.guild_id
        """
    )
    return [dict(row) for row in rows]


async def set_active_lobby_channel(
    guild_id: int,
    channel_id: int | None,
    updated_by: int,
) -> None:
    await get_pool().execute(
        """
        INSERT INTO guild_config (
            guild_id, active_lobby_channel_id,
            active_lobby_baseline_pending, updated_by
        )
        VALUES ($1, $2, TRUE, $3)
        ON CONFLICT (guild_id) DO UPDATE
        SET active_lobby_channel_id = EXCLUDED.active_lobby_channel_id,
            active_lobby_baseline_pending = CASE
                WHEN guild_config.active_lobby_channel_id IS NULL
                 AND EXCLUDED.active_lobby_channel_id IS NOT NULL
                THEN TRUE
                ELSE guild_config.active_lobby_baseline_pending
            END,
            updated_at = now(),
            updated_by = EXCLUDED.updated_by
        """,
        guild_id,
        channel_id,
        updated_by,
    )


async def complete_active_lobby_baseline(guild_id: int) -> None:
    await get_pool().execute(
        """
        UPDATE guild_config
        SET active_lobby_baseline_pending = FALSE,
            updated_at = now()
        WHERE guild_id = $1
          AND active_lobby_channel_id IS NOT NULL
        """,
        guild_id,
    )


async def set_active_lobby_role(
    guild_id: int,
    role_id: int | None,
    updated_by: int,
) -> None:
    await get_pool().execute(
        """
        INSERT INTO guild_config (guild_id, active_lobby_role_id, updated_by)
        VALUES ($1, $2, $3)
        ON CONFLICT (guild_id) DO UPDATE
        SET active_lobby_role_id = EXCLUDED.active_lobby_role_id,
            updated_at = now(),
            updated_by = EXCLUDED.updated_by
        """,
        guild_id,
        role_id,
        updated_by,
    )


async def set_website_moderator_role(
    guild_id: int,
    role_id: int | None,
    updated_by: int,
) -> None:
    await get_pool().execute(
        """
        INSERT INTO guild_config (guild_id, website_moderator_role_id, updated_by)
        VALUES ($1, $2, $3)
        ON CONFLICT (guild_id) DO UPDATE
        SET website_moderator_role_id = EXCLUDED.website_moderator_role_id,
            updated_at = now(),
            updated_by = EXCLUDED.updated_by
        """,
        guild_id,
        role_id,
        updated_by,
    )


async def reset_active_lobby_config(guild_id: int, updated_by: int) -> None:
    await get_pool().execute(
        """
        INSERT INTO guild_config (
            guild_id, active_lobby_channel_id, active_lobby_role_id,
            website_moderator_role_id,
            active_lobby_baseline_pending, updated_by
        )
        VALUES ($1, NULL, NULL, NULL, TRUE, $2)
        ON CONFLICT (guild_id) DO UPDATE
        SET active_lobby_channel_id = NULL,
            active_lobby_role_id = NULL,
            website_moderator_role_id = NULL,
            active_lobby_baseline_pending = TRUE,
            updated_at = now(),
            updated_by = EXCLUDED.updated_by
        """,
        guild_id,
        updated_by,
    )


async def get_current_leaderboard_ratings() -> tuple[tuple[int, int] | None, list[dict[str, Any]]]:
    """Return the most recently updated season, ordered only by descending MMR."""
    pool = get_pool()
    season = await pool.fetchrow(
        """
        SELECT season_year, season_number
        FROM season_ratings
        ORDER BY updated_at DESC
        LIMIT 1
        """
    )
    if season is None:
        return None, []

    rows = await pool.fetch(
        """
        SELECT
            ratings.user_id,
            ratings.season_year,
            ratings.season_number,
            ratings.mmr,
            ratings.wins,
            ratings.losses,
            ratings.matches_played,
            users.discord_username,
            users.discord_id
        FROM season_ratings AS ratings
        INNER JOIN users ON users.id = ratings.user_id
        WHERE ratings.season_year = $1 AND ratings.season_number = $2
        ORDER BY ratings.mmr DESC
        """,
        season["season_year"],
        season["season_number"],
    )
    season_key = (season["season_year"], season["season_number"])
    return season_key, [dict(row) for row in rows]


async def get_leaderboard_slots(guild_id: int) -> list[dict[str, Any]]:
    rows = await get_pool().fetch(
        """
        SELECT guild_id, slot, channel_id, message_id, season_year,
               season_number, user_id, fingerprint, updated_at
        FROM leaderboard_slots
        WHERE guild_id = $1
        ORDER BY slot
        """,
        guild_id,
    )
    return [dict(row) for row in rows]


async def replace_leaderboard_slots(
    guild_id: int,
    slots: Sequence[dict[str, Any]],
) -> None:
    """Atomically replace one guild's complete leaderboard metadata."""
    if not slots:
        raise ValueError("leaderboard slots must include the header")
    if [slot["slot"] for slot in slots] != list(range(len(slots))):
        raise ValueError("leaderboard slots must be ordered and contiguous from zero")

    records: list[tuple[Any, ...]] = []
    for slot in slots:
        channel_id = slot["channel_id"]
        message_id = slot["message_id"]
        if (channel_id is None) != (message_id is None):
            raise ValueError(
                "channel_id and message_id must both be set or both be None"
            )
        if slot["slot"] == 0 and slot["user_id"] is not None:
            raise ValueError("leaderboard header cannot have a user_id")
        records.append(
            (
                guild_id,
                slot["slot"],
                channel_id,
                message_id,
                slot["season_year"],
                slot["season_number"],
                (
                    uuid.UUID(str(slot["user_id"]))
                    if slot["user_id"] is not None
                    else None
                ),
                slot["fingerprint"],
            )
        )

    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM leaderboard_slots WHERE guild_id = $1",
                guild_id,
            )
            await conn.executemany(
                """
                INSERT INTO leaderboard_slots (
                    guild_id, slot, channel_id, message_id, season_year,
                    season_number, user_id, fingerprint
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                records,
            )


async def get_users() -> list[User]:
    pool = get_pool()
    rows = await pool.fetch("SELECT * FROM users")
    return [
        User(
            id=str(row["id"]),
            discord_username=row["discord_username"],
            discord_id=row["discord_id"],
            member_number=row["member_number"],
            bio=row["bio"],
            favorite_champion=row["favorite_champion"],
            profile_title=row["profile_title"],
            username_changed_at=row["username_changed_at"],
            tournaments_joined=row["tournaments_joined"],
        )
        for row in rows
    ]


async def set_user_discord_id(user_id: str | uuid.UUID, discord_id: int) -> bool:
    """Atomically dual-write a safe active-user Discord link."""
    target_id = uuid.UUID(str(user_id))
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock($1)", discord_id)
            if not await conn.fetchval("SELECT EXISTS(SELECT 1 FROM users WHERE id = $1)", target_id):
                return False
            active_owner = await conn.fetchval(
                """
                SELECT links.user_id
                FROM user_discord_links AS links
                INNER JOIN users ON users.id = links.user_id
                WHERE links.discord_id = $1 AND links.user_id <> $2
                """,
                discord_id,
                target_id,
            )
            if active_owner is not None:
                return False
            await conn.execute(
                "DELETE FROM user_discord_links WHERE discord_id = $1 AND user_id <> $2",
                discord_id,
                target_id,
            )
            await conn.execute(
                """
                INSERT INTO user_discord_links (user_id, discord_id)
                VALUES ($1, $2)
                ON CONFLICT (user_id) DO UPDATE
                SET discord_id = EXCLUDED.discord_id, updated_at = now()
                """,
                target_id,
                discord_id,
            )
            await conn.execute(
                "UPDATE users SET discord_id = NULL WHERE discord_id = $1 AND id <> $2",
                discord_id,
                target_id,
            )
            linked = await conn.fetchval(
                "UPDATE users SET discord_id = $2 WHERE id = $1 RETURNING TRUE",
                target_id,
                discord_id,
            )
            return bool(linked)


async def get_active_user_by_discord_id(discord_id: int) -> dict[str, Any] | None:
    row = await get_pool().fetchrow(
        """
        SELECT users.*
        FROM users
        LEFT JOIN user_discord_links AS links ON links.user_id = users.id
        WHERE links.discord_id = $1
           OR (
               users.discord_id = $1
               AND NOT EXISTS (
                   SELECT 1 FROM user_discord_links
                   WHERE user_discord_links.discord_id = $1
               )
           )
        ORDER BY (links.discord_id = $1) DESC
        LIMIT 1
        """,
        discord_id,
    )
    return dict(row) if row is not None else None


async def find_unlinked_active_users(candidate_names: Sequence[str]) -> list[dict[str, Any]]:
    normalized = sorted({name.strip().casefold() for name in candidate_names if name.strip()})
    if not normalized:
        return []
    rows = await get_pool().fetch(
        """
        SELECT users.*
        FROM users
        LEFT JOIN user_discord_links AS links ON links.user_id = users.id
        WHERE links.user_id IS NULL
          AND lower(btrim(users.discord_username)) = ANY($1::TEXT[])
        ORDER BY users.id
        """,
        normalized,
    )
    return [dict(row) for row in rows]


async def find_active_users_by_names(candidate_names: Sequence[str]) -> list[dict[str, Any]]:
    normalized = sorted({name.strip().casefold() for name in candidate_names if name.strip()})
    if not normalized:
        return []
    rows = await get_pool().fetch(
        """
        SELECT users.*, links.discord_id AS linked_discord_id
        FROM users
        LEFT JOIN user_discord_links AS links ON links.user_id = users.id
        WHERE lower(btrim(users.discord_username)) = ANY($1::TEXT[])
        ORDER BY users.id
        """,
        normalized,
    )
    return [dict(row) for row in rows]


async def get_current_rank_profile(user_id: str | uuid.UUID) -> dict[str, Any] | None:
    row = await get_pool().fetchrow(
        """
        WITH current_season AS (
            SELECT season_year, season_number
            FROM season_ratings
            ORDER BY updated_at DESC
            LIMIT 1
        )
        SELECT users.id, users.discord_id, users.discord_username,
               users.member_number, ratings.mmr, ratings.wins, ratings.losses,
               ratings.matches_played, ratings.season_year, ratings.season_number
        FROM users
        LEFT JOIN season_ratings AS ratings
          ON ratings.user_id = users.id
         AND (ratings.season_year, ratings.season_number) = (
             SELECT season_year, season_number FROM current_season
         )
        WHERE users.id = $1
        """,
        uuid.UUID(str(user_id)),
    )
    return dict(row) if row is not None else None


async def set_rank_cooldown_seconds(guild_id: int, seconds: int, updated_by: int) -> None:
    if not 0 <= seconds <= 3600:
        raise ValueError("rank cooldown must be between 0 and 3600 seconds")
    await get_pool().execute(
        """
        INSERT INTO guild_config (guild_id, rank_cooldown_seconds, updated_by)
        VALUES ($1, $2, $3)
        ON CONFLICT (guild_id) DO UPDATE
        SET rank_cooldown_seconds = EXCLUDED.rank_cooldown_seconds,
            updated_at = now(), updated_by = EXCLUDED.updated_by
        """,
        guild_id, seconds, updated_by,
    )


async def get_rank_cooldown_seconds(guild_id: int) -> int:
    seconds = await get_pool().fetchval(
        "SELECT rank_cooldown_seconds FROM guild_config WHERE guild_id = $1",
        guild_id,
    )
    return 20 if seconds is None else seconds


async def set_command_channel_rule(guild_id: int, channel_id: int, rule: str, updated_by: int) -> None:
    if rule not in {"allow", "block"}:
        raise ValueError("command channel rule must be allow or block")
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO guild_config (guild_id, updated_by) VALUES ($1, $2) ON CONFLICT (guild_id) DO NOTHING",
                guild_id, updated_by,
            )
            await conn.execute(
                """
                INSERT INTO guild_command_channels (guild_id, channel_id, rule, updated_by)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (guild_id, channel_id) DO UPDATE
                SET rule = EXCLUDED.rule, updated_by = EXCLUDED.updated_by, updated_at = now()
                """,
                guild_id, channel_id, rule, updated_by,
            )


async def remove_command_channel_rule(guild_id: int, channel_id: int) -> bool:
    result = await get_pool().execute(
        "DELETE FROM guild_command_channels WHERE guild_id = $1 AND channel_id = $2",
        guild_id, channel_id,
    )
    return result != "DELETE 0"


async def get_command_channel_rules(guild_id: int) -> list[dict[str, Any]]:
    rows = await get_pool().fetch(
        "SELECT guild_id, channel_id, rule, updated_by, updated_at FROM guild_command_channels WHERE guild_id = $1 ORDER BY rule, channel_id",
        guild_id,
    )
    return [dict(row) for row in rows]


async def record_created_role(guild_id: int, purpose: str, role_id: int, created_by: int) -> None:
    await get_pool().execute(
        """
        INSERT INTO guild_created_roles (guild_id, purpose, role_id, created_by)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (guild_id, purpose) DO UPDATE
        SET role_id = EXCLUDED.role_id, created_by = EXCLUDED.created_by, created_at = now()
        """,
        guild_id, purpose, role_id, created_by,
    )


async def get_created_role(guild_id: int, purpose: str) -> dict[str, Any] | None:
    row = await get_pool().fetchrow(
        "SELECT guild_id, purpose, role_id, created_by, created_at FROM guild_created_roles WHERE guild_id = $1 AND purpose = $2",
        guild_id, purpose,
    )
    return dict(row) if row is not None else None


async def forget_created_role(guild_id: int, purpose: str, role_id: int | None = None) -> bool:
    result = await get_pool().execute(
        "DELETE FROM guild_created_roles WHERE guild_id = $1 AND purpose = $2 AND ($3::BIGINT IS NULL OR role_id = $3)",
        guild_id, purpose, role_id,
    )
    return result != "DELETE 0"


async def get_lobbies() -> list[Lobby]:
    pool = get_pool()
    rows = await pool.fetch("SELECT * FROM lobbies")

    return [
        Lobby(
            id=row["id"],
            lobby_number=row["lobby_number"],
            title=row["title"],
            lobby_type=row["lobby_type"],
            region=row["region"],
            match_size=row["match_size"],
            team_one_name=row["team_one_name"],
            team_two_name=row["team_two_name"],
            creator_id=str(row["creator_id"]) if row["creator_id"] else None,
            status=row["status"],
            draft_step=row["draft_step"],
            draft_started_at=row["draft_started_at"],
            winner_slot=row["winner_slot"],
            season_year=row["season_year"],
            season_number=row["season_number"],
            season_name=row["season_name"],
            created_at=row["created_at"],
            ended_at=row["ended_at"],
            match_started_at=row["match_started_at"],
            dispute_reason=row["dispute_reason"],
            winner_confirmed_by_team_one=row["winner_confirmed_by_team_one"],
            winner_confirmed_by_team_two=row["winner_confirmed_by_team_two"],
            result_team_one_vote=row["result_team_one_vote"],
            result_team_two_vote=row["result_team_two_vote"],
            discord_match_ready_requested_at=row["discord_match_ready_requested_at"],
            discord_match_ready_sent_at=row["discord_match_ready_sent_at"],
            discord_match_ready_status=row["discord_match_ready_status"],
            discord_match_ready_error=row["discord_match_ready_error"],
            mmr_applied=row["mmr_applied"],
            ban_count=row["ban_count"],
            is_tournament=row["is_tournament"],
            tournament_match_id=row["tournament_match_id"],
            tournament_name=row["tournament_name"],
            url_year=row["url_year"],
            url_series=row["url_series"],
            game_number=row["game_number"],
            has_password=row["has_password"],
            map_pool=row["map_pool"],
            selected_map=row["selected_map"],
            team_one_roster=[],
            team_two_roster=[],
        )
        for row in rows
    ]


async def get_active_lobby_candidates() -> list[dict[str, Any]]:
    """Load globally relevant lobbies with team and locally linked user state."""
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                lobbies.*,
                COALESCE(team_one.roster, ARRAY[]::UUID[]) AS team_one_roster,
                COALESCE(team_two.roster, ARRAY[]::UUID[]) AS team_two_roster,
                team_one.captain_id AS team_one_captain_id,
                team_two.captain_id AS team_two_captain_id,
                COALESCE(team_one.picks, ARRAY[]::TEXT[]) AS team_one_picks,
                COALESCE(team_two.picks, ARRAY[]::TEXT[]) AS team_two_picks,
                COALESCE(team_one.bans, ARRAY[]::TEXT[]) AS team_one_bans,
                COALESCE(team_two.bans, ARRAY[]::TEXT[]) AS team_two_bans,
                CASE
                    WHEN team_one.draft_finalized_at IS NOT NULL
                     AND team_two.draft_finalized_at IS NOT NULL
                    THEN GREATEST(
                        team_one.draft_finalized_at,
                        team_two.draft_finalized_at
                    )
                    ELSE NULL
                END AS draft_finalized_at
            FROM lobbies
            LEFT JOIN lobby_rosters AS team_one
              ON team_one.lobby_id = lobbies.id
             AND team_one.team = 'team_one'
            LEFT JOIN lobby_rosters AS team_two
              ON team_two.lobby_id = lobbies.id
             AND team_two.team = 'team_two'
            WHERE lobbies.ended_at IS NULL
               OR lower(lobbies.status) IN (
                    'awaiting_result', 'awaiting_results',
                    'awaiting_votes', 'disputed'
               )
               OR EXISTS (
                    SELECT 1
                    FROM active_lobby_posts AS posts
                    WHERE posts.lobby_id = lobbies.id
                      AND posts.message_id IS NOT NULL
               )
            ORDER BY lobbies.created_at, lobbies.id
            """
        )

        referenced_user_ids: set[uuid.UUID] = set()
        candidates: list[dict[str, Any]] = []
        for row in rows:
            candidate = dict(row)
            if candidate["creator_id"] is not None:
                candidate["creator_id"] = str(candidate["creator_id"])
            for key in ("team_one_roster", "team_two_roster"):
                candidate[key] = [str(user_id) for user_id in candidate[key]]
                referenced_user_ids.update(
                    uuid.UUID(user_id) for user_id in candidate[key]
                )
            for key in ("team_one_captain_id", "team_two_captain_id"):
                captain_id = candidate[key]
                if captain_id is not None:
                    referenced_user_ids.add(captain_id)
                    candidate[key] = str(captain_id)
            candidates.append(candidate)

        users_by_id: dict[str, dict[str, Any]] = {}
        if referenced_user_ids:
            user_rows = await conn.fetch(
                """
                SELECT id, discord_id, discord_username
                FROM users
                WHERE id = ANY($1::UUID[])
                """,
                list(referenced_user_ids),
            )
            users_by_id = {
                str(row["id"]): {
                    "id": str(row["id"]),
                    "discord_id": row["discord_id"],
                    "discord_username": row["discord_username"],
                }
                for row in user_rows
            }

    for candidate in candidates:
        user_ids = set(candidate["team_one_roster"])
        user_ids.update(candidate["team_two_roster"])
        if candidate["team_one_captain_id"] is not None:
            user_ids.add(candidate["team_one_captain_id"])
        if candidate["team_two_captain_id"] is not None:
            user_ids.add(candidate["team_two_captain_id"])
        candidate["users_by_id"] = {
            user_id: users_by_id[user_id]
            for user_id in user_ids
            if user_id in users_by_id
        }
    return candidates


async def get_lobby_ids_for_user(user_id: str | uuid.UUID) -> list[int]:
    rows = await get_pool().fetch(
        """
        SELECT lobby_id
        FROM lobby_rosters
        WHERE $1 = ANY(roster)
        ORDER BY lobby_id
        """,
        uuid.UUID(str(user_id)),
    )
    return [row["lobby_id"] for row in rows]


async def update_lobby_captains(
    lobby_id: int,
    captains: Sequence[LobbyCaptain],
) -> None:
    by_slot: dict[str, LobbyCaptain] = {}
    for captain in captains:
        if captain.slot not in {"team_one", "team_two"}:
            raise ValueError(f"Unsupported captain slot: {captain.slot}")
        if captain.slot in by_slot:
            raise ValueError(f"Duplicate captain slot: {captain.slot}")
        by_slot[captain.slot] = captain

    if not by_slot:
        return

    await get_pool().executemany(
        """
        INSERT INTO lobby_rosters (lobby_id, team, captain_id)
        VALUES ($1, $2, $3)
        ON CONFLICT (lobby_id, team) DO UPDATE
        SET captain_id = EXCLUDED.captain_id
        """,
        [
            (lobby_id, slot, uuid.UUID(captain.user_id))
            for slot, captain in by_slot.items()
        ],
    )


async def finalize_lobby_draft(
    lobby_id: int,
    actions: Sequence[LobbyDraftAction],
    finalized_at: datetime | None = None,
) -> None:
    draft: dict[str, dict[str, list[tuple[int, str]]]] = {
        "team_one": {"pick": [], "ban": []},
        "team_two": {"pick": [], "ban": []},
    }
    for action in actions:
        if action.lobby_id != lobby_id:
            raise ValueError("Draft action belongs to a different lobby")
        if action.action == "map_ban":
            continue
        if action.action not in {"pick", "ban"}:
            continue
        if action.team_slot not in draft:
            raise ValueError(f"Unsupported draft team slot: {action.team_slot}")
        if action.champion:
            draft[action.team_slot][action.action].append(
                (action.step, action.champion)
            )

    completed_at = finalized_at or datetime.now(timezone.utc)
    records = []
    for team in ("team_one", "team_two"):
        picks = [
            champion
            for _, champion in sorted(draft[team]["pick"], key=lambda item: item[0])
        ]
        bans = [
            champion
            for _, champion in sorted(draft[team]["ban"], key=lambda item: item[0])
        ]
        records.append((lobby_id, team, picks, bans, completed_at))

    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.executemany(
                """
                INSERT INTO lobby_rosters (
                    lobby_id, team, picks, bans, draft_finalized_at
                )
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (lobby_id, team) DO UPDATE
                SET picks = EXCLUDED.picks,
                    bans = EXCLUDED.bans,
                    draft_finalized_at = COALESCE(
                        lobby_rosters.draft_finalized_at,
                        EXCLUDED.draft_finalized_at
                    )
                """,
                records,
            )


async def get_active_lobby_post_states(guild_id: int) -> list[dict[str, Any]]:
    rows = await get_pool().fetch(
        """
        SELECT guild_id, lobby_id, channel_id, message_id, fingerprint,
               first_seen_at, notification_handled,
               dispute_notification_handled, updated_at
        FROM active_lobby_posts
        WHERE guild_id = $1
        ORDER BY lobby_id
        """,
        guild_id,
    )
    return [dict(row) for row in rows]


async def ensure_active_lobby_post_states(
    guild_id: int,
    lobby_ids: Sequence[int],
    *,
    notification_handled: bool,
    dispute_notification_handled: bool,
) -> None:
    unique_lobby_ids = list(dict.fromkeys(lobby_ids))
    if not unique_lobby_ids:
        return
    await get_pool().executemany(
        """
        INSERT INTO active_lobby_posts (
            guild_id, lobby_id, notification_handled,
            dispute_notification_handled
        )
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (guild_id, lobby_id) DO UPDATE
        SET notification_handled = (
                active_lobby_posts.notification_handled
                OR EXCLUDED.notification_handled
            ),
            dispute_notification_handled = (
                active_lobby_posts.dispute_notification_handled
                OR EXCLUDED.dispute_notification_handled
            ),
            updated_at = now()
        WHERE (
            NOT active_lobby_posts.notification_handled
            AND EXCLUDED.notification_handled
        ) OR (
            NOT active_lobby_posts.dispute_notification_handled
            AND EXCLUDED.dispute_notification_handled
        )
        """,
        [
            (
                guild_id,
                lobby_id,
                notification_handled,
                dispute_notification_handled,
            )
            for lobby_id in unique_lobby_ids
        ],
    )


async def record_active_lobby_post(
    guild_id: int,
    lobby_id: int,
    channel_id: int,
    message_id: int,
    fingerprint: str,
    *,
    notification_handled: bool,
    dispute_notification_handled: bool,
) -> None:
    await get_pool().execute(
        """
        INSERT INTO active_lobby_posts (
            guild_id, lobby_id, channel_id, message_id, fingerprint,
            notification_handled, dispute_notification_handled
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        ON CONFLICT (guild_id, lobby_id) DO UPDATE
        SET channel_id = EXCLUDED.channel_id,
            message_id = EXCLUDED.message_id,
            fingerprint = EXCLUDED.fingerprint,
            notification_handled = (
                active_lobby_posts.notification_handled
                OR EXCLUDED.notification_handled
            ),
            dispute_notification_handled = (
                active_lobby_posts.dispute_notification_handled
                OR EXCLUDED.dispute_notification_handled
            ),
            updated_at = now()
        """,
        guild_id,
        lobby_id,
        channel_id,
        message_id,
        fingerprint,
        notification_handled,
        dispute_notification_handled,
    )


async def clear_active_lobby_post_message(
    guild_id: int,
    lobby_id: int,
    channel_id: int,
    message_id: int,
) -> bool:
    cleared = await get_pool().fetchval(
        """
        UPDATE active_lobby_posts
        SET channel_id = NULL,
            message_id = NULL,
            fingerprint = NULL,
            updated_at = now()
        WHERE guild_id = $1
          AND lobby_id = $2
          AND channel_id = $3
          AND message_id = $4
        RETURNING TRUE
        """,
        guild_id,
        lobby_id,
        channel_id,
        message_id,
    )
    return bool(cleared)


async def get_active_lobby_empty_post(guild_id: int) -> dict[str, Any] | None:
    row = await get_pool().fetchrow(
        """
        SELECT guild_id, channel_id, message_id, fingerprint, updated_at
        FROM active_lobby_empty_posts
        WHERE guild_id = $1
        """,
        guild_id,
    )
    return dict(row) if row is not None else None


async def record_active_lobby_empty_post(
    guild_id: int,
    channel_id: int,
    message_id: int,
    fingerprint: str,
) -> None:
    await get_pool().execute(
        """
        INSERT INTO active_lobby_empty_posts (
            guild_id, channel_id, message_id, fingerprint
        )
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (guild_id) DO UPDATE
        SET channel_id = EXCLUDED.channel_id,
            message_id = EXCLUDED.message_id,
            fingerprint = EXCLUDED.fingerprint,
            updated_at = now()
        """,
        guild_id,
        channel_id,
        message_id,
        fingerprint,
    )


async def clear_active_lobby_empty_post(
    guild_id: int,
    channel_id: int,
    message_id: int,
) -> bool:
    cleared = await get_pool().fetchval(
        """
        UPDATE active_lobby_empty_posts
        SET channel_id = NULL,
            message_id = NULL,
            fingerprint = NULL,
            updated_at = now()
        WHERE guild_id = $1
          AND channel_id = $2
          AND message_id = $3
        RETURNING TRUE
        """,
        guild_id,
        channel_id,
        message_id,
    )
    return bool(cleared)


async def get_active_lobby_obsolete_posts(
    guild_id: int,
) -> list[dict[str, Any]]:
    rows = await get_pool().fetch(
        """
        SELECT guild_id, lobby_id, channel_id, message_id, created_at
        FROM active_lobby_obsolete_posts
        WHERE guild_id = $1
        ORDER BY created_at, channel_id, message_id
        """,
        guild_id,
    )
    return [dict(row) for row in rows]


async def record_active_lobby_obsolete_post(
    guild_id: int,
    channel_id: int,
    message_id: int,
    lobby_id: int | None,
) -> None:
    await get_pool().execute(
        """
        INSERT INTO active_lobby_obsolete_posts (
            guild_id, lobby_id, channel_id, message_id
        )
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (guild_id, channel_id, message_id) DO NOTHING
        """,
        guild_id,
        lobby_id,
        channel_id,
        message_id,
    )


async def delete_active_lobby_obsolete_post(
    guild_id: int,
    channel_id: int,
    message_id: int,
) -> bool:
    deleted = await get_pool().fetchval(
        """
        DELETE FROM active_lobby_obsolete_posts
        WHERE guild_id = $1
          AND channel_id = $2
          AND message_id = $3
        RETURNING TRUE
        """,
        guild_id,
        channel_id,
        message_id,
    )
    return bool(deleted)


async def get_season_ratings() -> list[SeasonRating]:
    pool = get_pool()
    rows = await pool.fetch("SELECT * FROM season_ratings")

    return [
        SeasonRating(
            id=row["id"],
            user_id=str(row["user_id"]),
            season_year=row["season_year"],
            season_number=row["season_number"],
            mmr=row["mmr"],
            wins=row["wins"],
            losses=row["losses"],
            matches_played=row["matches_played"],
            updated_at=row["updated_at"],
        )
        for row in rows
    ]


async def sync_battlevive_data_to_db(
    users: list[User] | None = None,
    lobbies: list[Lobby] | None = None,
    season_ratings: list[SeasonRating] | None = None,
) -> list[uuid.UUID]:
    """
    Writes fetched data to Postgres. All arguments are optional; only
    datasets that are provided get synced.

    users is awaited first, since lobbies.creator_id, lobby_rosters.user_id,
    and season_ratings.user_id all foreign-key into users. lobbies and
    season_ratings don't depend on each other, so once users is done they
    run concurrently.
    """
    logger.info("Syncing local db with upstream")

    deleted_user_ids: list[uuid.UUID] = []
    if users is not None:
        authoritative_ids = _validated_user_ids(users)
        deleted_user_ids = await sync_users_to_db(users)
        if lobbies is not None and all(isinstance(lobby, Lobby) for lobby in lobbies):
            lobbies = sanitize_lobbies(lobbies, authoritative_ids)
        if season_ratings is not None and all(
            isinstance(rating, SeasonRating) for rating in season_ratings
        ):
            season_ratings = sanitize_season_ratings(
                season_ratings,
                authoritative_ids,
            )

    if users is None and (lobbies is not None or season_ratings is not None):
        referenced_ids: set[uuid.UUID] = set()
        if lobbies is not None and all(isinstance(lobby, Lobby) for lobby in lobbies):
            for lobby in lobbies:
                if lobby.creator_id:
                    referenced_ids.add(uuid.UUID(str(lobby.creator_id)))
                referenced_ids.update(uuid.UUID(str(item)) for item in lobby.team_one_roster)
                referenced_ids.update(uuid.UUID(str(item)) for item in lobby.team_two_roster)
        if season_ratings is not None and all(
            isinstance(rating, SeasonRating) for rating in season_ratings
        ):
            referenced_ids.update(uuid.UUID(str(rating.user_id)) for rating in season_ratings)
        if referenced_ids:
            missing = await get_pool().fetchval(
                """
                SELECT requested.user_id
                FROM unnest($1::UUID[]) AS requested(user_id)
                LEFT JOIN users ON users.id = requested.user_id
                WHERE users.id IS NULL LIMIT 1
                """,
                list(referenced_ids),
            )
            if missing is not None:
                raise MissingUsersError(
                    "Fetched data references users that are missing locally"
                )

    tasks = []
    if lobbies is not None:
        tasks.append(sync_lobbies_to_db(lobbies))
    if season_ratings is not None:
        tasks.append(sync_season_ratings_to_db(season_ratings))

    if not tasks:
        return deleted_user_ids

    results = await asyncio.gather(*tasks, return_exceptions=True)
    for result in results:
        if isinstance(result, asyncpg.ForeignKeyViolationError):
            raise MissingUsersError(
                "Fetched data references users that are missing locally"
            ) from result
    for result in results:
        if isinstance(result, BaseException):
            raise result
    return deleted_user_ids


def _validated_user_ids(users: Sequence[User]) -> set[uuid.UUID]:
    ids = [uuid.UUID(str(user.id)) for user in users]
    if len(ids) != len(set(ids)):
        raise ValueError("users response contains duplicate UUIDs")
    return set(ids)


def sanitize_season_ratings(
    ratings: Sequence[SeasonRating],
    authoritative_ids: set[uuid.UUID],
) -> list[SeasonRating]:
    sanitized = []
    for rating in ratings:
        try:
            user_id = uuid.UUID(str(rating.user_id))
        except (TypeError, ValueError, AttributeError):
            logger.warning("Dropping rating %s with an invalid user UUID.", rating.id)
            continue
        if user_id not in authoritative_ids:
            logger.warning("Dropping rating %s for absent user %s.", rating.id, user_id)
            continue
        sanitized.append(rating)
    return sanitized


def sanitize_lobbies(
    lobbies: Sequence[Lobby],
    authoritative_ids: set[uuid.UUID],
) -> list[Lobby]:
    sanitized = []
    for lobby in lobbies:
        def retained(values: Sequence[str]) -> list[str]:
            result = []
            for value in values:
                try:
                    parsed = uuid.UUID(str(value))
                except (TypeError, ValueError, AttributeError):
                    continue
                if parsed in authoritative_ids:
                    result.append(str(parsed))
            return result

        creator_id = lobby.creator_id
        if creator_id is not None:
            try:
                if uuid.UUID(str(creator_id)) not in authoritative_ids:
                    creator_id = None
            except (TypeError, ValueError, AttributeError):
                creator_id = None
        sanitized.append(
            replace(
                lobby,
                creator_id=creator_id,
                team_one_roster=retained(lobby.team_one_roster),
                team_two_roster=retained(lobby.team_two_roster),
            )
        )
    return sanitized


async def sync_users_to_db(users: list[User]) -> list[uuid.UUID]:
    authoritative_ids = _validated_user_ids(users)

    pool = get_pool()
    records = [
        (
            uuid.UUID(str(user.id)),
            user.discord_username,
            user.member_number,
            user.tournaments_joined,
            user.bio,
            user.favorite_champion,
            user.profile_title,
            user.username_changed_at,
        )
        for user in users
    ]
    async with pool.acquire() as conn:
        async with conn.transaction():
            if records:
                await conn.executemany(
                    """
                    INSERT INTO users (
                        id, discord_username, member_number,
                        tournaments_joined, bio, favorite_champion,
                        profile_title, username_changed_at
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    ON CONFLICT (id) DO UPDATE
                    SET discord_username    = EXCLUDED.discord_username,
                        member_number       = EXCLUDED.member_number,
                        tournaments_joined  = EXCLUDED.tournaments_joined,
                        bio                 = EXCLUDED.bio,
                        favorite_champion   = EXCLUDED.favorite_champion,
                        profile_title       = EXCLUDED.profile_title,
                        username_changed_at = EXCLUDED.username_changed_at
                    """,
                    records,
                )
            await conn.execute(
                """
                UPDATE users
                SET discord_id = links.discord_id
                FROM user_discord_links AS links
                WHERE users.id = links.user_id
                  AND users.id = ANY($1::UUID[])
                """,
                list(authoritative_ids),
            )
            await conn.execute(
                """
                INSERT INTO user_discord_links (user_id, discord_id)
                SELECT id, discord_id FROM users WHERE discord_id IS NOT NULL
                ON CONFLICT DO NOTHING
                """
            )
            deleted = await conn.fetch(
                "SELECT id FROM users WHERE NOT (id = ANY($1::UUID[]))",
                list(authoritative_ids),
            )
            if deleted:
                deleted_ids = [row["id"] for row in deleted]
                await conn.execute(
                    """
                    UPDATE lobby_rosters
                    SET roster = ARRAY(
                        SELECT member_id FROM unnest(roster) AS member_id
                        WHERE NOT (member_id = ANY($1::UUID[]))
                    ),
                    captain_id = CASE
                        WHEN captain_id = ANY($1::UUID[]) THEN NULL
                        ELSE captain_id
                    END
                    WHERE roster && $1::UUID[] OR captain_id = ANY($1::UUID[])
                    """,
                    deleted_ids,
                )
                await conn.execute("DELETE FROM users WHERE id = ANY($1::UUID[])", deleted_ids)
            return [row["id"] for row in deleted]


async def sync_lobbies_to_db(lobbies: list[Lobby]) -> None:
    pool = get_pool()
    upstream_ids = [lobby.id for lobby in lobbies]
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.executemany(
                """
                INSERT INTO lobbies (
                    id, lobby_number, title, lobby_type, region, match_size,
                    team_one_name, team_two_name, creator_id, status,
                    draft_step, draft_started_at, winner_slot,
                    season_year, season_number, season_name,
                    created_at, ended_at, match_started_at,
                    dispute_reason, winner_confirmed_by_team_one, winner_confirmed_by_team_two,
                    result_team_one_vote, result_team_two_vote,
                    discord_match_ready_requested_at, discord_match_ready_sent_at,
                    discord_match_ready_status, discord_match_ready_error,
                    mmr_applied, ban_count, is_tournament, tournament_match_id, tournament_name,
                    url_year, url_series, game_number, has_password, map_pool, selected_map
                )
                VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                    $11, $12, $13, $14, $15, $16, $17, $18, $19,
                    $20, $21, $22, $23, $24, $25, $26, $27, $28,
                    $29, $30, $31, $32, $33, $34, $35, $36, $37, $38, $39
                )
                ON CONFLICT (id) DO UPDATE
                SET lobby_number                      = EXCLUDED.lobby_number,
                    title                              = EXCLUDED.title,
                    lobby_type                         = EXCLUDED.lobby_type,
                    region                             = EXCLUDED.region,
                    match_size                         = EXCLUDED.match_size,
                    team_one_name                      = EXCLUDED.team_one_name,
                    team_two_name                      = EXCLUDED.team_two_name,
                    creator_id                         = EXCLUDED.creator_id,
                    status                              = EXCLUDED.status,
                    draft_step                         = EXCLUDED.draft_step,
                    draft_started_at                   = EXCLUDED.draft_started_at,
                    winner_slot                        = EXCLUDED.winner_slot,
                    season_year                        = EXCLUDED.season_year,
                    season_number                      = EXCLUDED.season_number,
                    season_name                        = EXCLUDED.season_name,
                    ended_at                           = EXCLUDED.ended_at,
                    match_started_at                   = EXCLUDED.match_started_at,
                    dispute_reason                      = EXCLUDED.dispute_reason,
                    winner_confirmed_by_team_one        = EXCLUDED.winner_confirmed_by_team_one,
                    winner_confirmed_by_team_two        = EXCLUDED.winner_confirmed_by_team_two,
                    result_team_one_vote                = EXCLUDED.result_team_one_vote,
                    result_team_two_vote                = EXCLUDED.result_team_two_vote,
                    discord_match_ready_requested_at    = EXCLUDED.discord_match_ready_requested_at,
                    discord_match_ready_sent_at         = EXCLUDED.discord_match_ready_sent_at,
                    discord_match_ready_status          = EXCLUDED.discord_match_ready_status,
                    discord_match_ready_error           = EXCLUDED.discord_match_ready_error,
                    mmr_applied                         = EXCLUDED.mmr_applied,
                    ban_count                           = EXCLUDED.ban_count,
                    is_tournament                       = EXCLUDED.is_tournament,
                    tournament_match_id                 = EXCLUDED.tournament_match_id,
                    tournament_name                     = EXCLUDED.tournament_name,
                    url_year                            = EXCLUDED.url_year,
                    url_series                          = EXCLUDED.url_series,
                    game_number                         = EXCLUDED.game_number,
                    has_password                        = EXCLUDED.has_password,
                    map_pool                            = EXCLUDED.map_pool,
                    selected_map                        = EXCLUDED.selected_map
                """,
                [
                    (
                        lobby.id,
                        lobby.lobby_number,
                        lobby.title,
                        lobby.lobby_type,
                        lobby.region,
                        lobby.match_size,
                        lobby.team_one_name,
                        lobby.team_two_name,
                        uuid.UUID(lobby.creator_id) if lobby.creator_id else None,
                        lobby.status,
                        lobby.draft_step,
                        lobby.draft_started_at,
                        lobby.winner_slot,
                        lobby.season_year,
                        lobby.season_number,
                        lobby.season_name,
                        lobby.created_at,
                        lobby.ended_at,
                        lobby.match_started_at,
                        lobby.dispute_reason,
                        lobby.winner_confirmed_by_team_one,
                        lobby.winner_confirmed_by_team_two,
                        lobby.result_team_one_vote,
                        lobby.result_team_two_vote,
                        lobby.discord_match_ready_requested_at,
                        lobby.discord_match_ready_sent_at,
                        lobby.discord_match_ready_status,
                        lobby.discord_match_ready_error,
                        lobby.mmr_applied,
                        lobby.ban_count,
                        lobby.is_tournament,
                        lobby.tournament_match_id,
                        lobby.tournament_name,
                        lobby.url_year,
                        lobby.url_series,
                        lobby.game_number,
                        lobby.has_password,
                        lobby.map_pool,
                        lobby.selected_map,
                    )
                    for lobby in lobbies
                ],
            )

            roster_user_ids = {
                uuid.UUID(user_id)
                for lobby in lobbies
                for user_id in lobby.team_one_roster + lobby.team_two_roster
            }
            if roster_user_ids:
                missing_user = await conn.fetchval(
                    """
                    SELECT requested.user_id
                    FROM unnest($1::UUID[]) AS requested(user_id)
                    LEFT JOIN users ON users.id = requested.user_id
                    WHERE users.id IS NULL
                    LIMIT 1
                    """,
                    list(roster_user_ids),
                )
                if missing_user is not None:
                    raise asyncpg.ForeignKeyViolationError(
                        "lobby roster references a missing user"
                    )

            roster_rows = [
                (
                    lobby.id,
                    "team_one",
                    [uuid.UUID(user_id) for user_id in lobby.team_one_roster],
                )
                for lobby in lobbies
            ] + [
                (
                    lobby.id,
                    "team_two",
                    [uuid.UUID(user_id) for user_id in lobby.team_two_roster],
                )
                for lobby in lobbies
            ]
            await conn.executemany(
                """
                INSERT INTO lobby_rosters (lobby_id, team, roster)
                VALUES ($1, $2, $3)
                ON CONFLICT (lobby_id, team) DO UPDATE
                SET roster = EXCLUDED.roster
                """,
                roster_rows,
            )

            # The upstream list is authoritative. Preserve Discord references
            # before the lobby FK cascade removes per-guild post state.
            await conn.execute(
                """
                INSERT INTO active_lobby_obsolete_posts (
                    guild_id, lobby_id, channel_id, message_id
                )
                SELECT guild_id, lobby_id, channel_id, message_id
                FROM active_lobby_posts
                WHERE message_id IS NOT NULL
                  AND NOT (lobby_id = ANY($1::INTEGER[]))
                ON CONFLICT (guild_id, channel_id, message_id) DO NOTHING
                """,
                upstream_ids,
            )
            await conn.execute(
                """
                DELETE FROM lobbies
                WHERE NOT (id = ANY($1::INTEGER[]))
                """,
                upstream_ids,
            )


async def sync_season_ratings_to_db(ratings: list[SeasonRating]) -> None:
    if not ratings:
        return

    pool = get_pool()
    await pool.executemany(
        """
        INSERT INTO season_ratings (
            id, user_id, season_year, season_number,
            mmr, wins, losses, matches_played, updated_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        ON CONFLICT (id) DO UPDATE
        SET mmr             = EXCLUDED.mmr,
            wins            = EXCLUDED.wins,
            losses          = EXCLUDED.losses,
            matches_played  = EXCLUDED.matches_played,
            updated_at      = EXCLUDED.updated_at
        """,
        [
            (
                rating.id,
                uuid.UUID(rating.user_id),
                rating.season_year,
                rating.season_number,
                rating.mmr,
                rating.wins,
                rating.losses,
                rating.matches_played,
                rating.updated_at,
            )
            for rating in ratings
        ],
    )

from __future__ import annotations

import uuid

# pyrefly: ignore [missing-import]
import asyncpg

from .logs import logger
from .models import Lobby
from .models import SeasonRating
from .models import User


_pool: asyncpg.Pool | None = None


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


async def get_guild_config(guild_id: int) -> dict[str, int | None] | None:
    pool = get_pool()
    row = await pool.fetchrow(
        """
        SELECT guild_id, leaderboard_channel_id, updated_by
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


async def reset_guild_config(guild_id: int, updated_by: int) -> None:
    await upsert_guild_config(guild_id, None, updated_by)


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
    users: list[User],
    lobbies: list[Lobby],
    season_ratings: list[SeasonRating],
) -> None:
    """
    Writes fetched data to Postgres in dependency order: users first, since
    lobbies.creator_id, lobby_rosters.user_id, and season_ratings.user_id all
    foreign-key into users.
    """
    logger.info("Syncing local db with upstream")
    await sync_users_to_db(users)
    await sync_lobbies_to_db(lobbies)
    await sync_season_ratings_to_db(season_ratings)


async def sync_users_to_db(users: list[User]) -> None:
    if not users:
        return

    pool = get_pool()
    await pool.executemany(
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
        [
            (
                uuid.UUID(user.id),
                user.discord_username,
                user.member_number,
                user.tournaments_joined,
                user.bio,
                user.favorite_champion,
                user.profile_title,
                user.username_changed_at,
            )
            for user in users
        ],
    )


async def sync_lobbies_to_db(lobbies: list[Lobby]) -> None:
    if not lobbies:
        return

    pool = get_pool()
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

            lobby_ids = [lobby.id for lobby in lobbies]
            await conn.execute(
                "DELETE FROM lobby_rosters WHERE lobby_id = ANY($1::int[])",
                lobby_ids,
            )

            roster_rows = [
                (lobby.id, uuid.UUID(user_id), "team_one")
                for lobby in lobbies
                for user_id in lobby.team_one_roster
            ] + [
                (lobby.id, uuid.UUID(user_id), "team_two")
                for lobby in lobbies
                for user_id in lobby.team_two_roster
            ]
            if roster_rows:
                await conn.executemany(
                    "INSERT INTO lobby_rosters (lobby_id, user_id, team) VALUES ($1, $2, $3)",
                    roster_rows,
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

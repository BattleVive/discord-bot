# db.py
# pyrefly: ignore [missing-import]
import asyncpg
from logs import logger
from typing import List
from battlevive_api import User,Lobby,SeasonRating
_pool: asyncpg.Pool | None = None


async def init_pool(dsn: str) -> asyncpg.Pool:
    global _pool
    _pool = await asyncpg.create_pool(
        dsn=dsn,
        min_size=1,
        max_size=5,  # 2-5 guilds, single bot process – no need for a large pool
        command_timeout=10,
    )
    logger.info("PostgreSQL connection pool created.")
    return _pool


async def close_pool() -> None:
    if _pool is not None:
        await _pool.close()
        logger.info("PostgreSQL connection pool closed.")


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Connection pool not initialized. Call init_pool() first.")
    return _pool


async def get_users() -> List[User]:
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


async def get_lobbies() -> List[Lobby]:
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


async def get_season_ratings() -> List[SeasonRating]:
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


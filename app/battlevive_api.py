#!/usr/bin/env python3
# pyrefly: ignore [missing-import]
from dotenv import load_dotenv
# pyrefly: ignore [missing-import]
import aiohttp
import os,requests,json,time,uuid
from datetime import datetime
from dataclasses import dataclass, field,asdict
from typing import Optional, List
from logs import logger

load_dotenv()
SUPABASE_API_KEY = os.getenv("SUPABASE_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")

class BattlevivieTokenManager:
    def __init__(self, JWT_token, refresh_token):
        self.JWT_token = JWT_token 
        self.refresh_token = refresh_token
    
    def revalidate(refresh_token):
        headers = {
            "Content-Type": "application/json",
            "apikey": SUPABASE_API_KEY
        }
        json = {
            "refresh_token": refresh_token 
        }
        logger.debug(f"Revalidate headers: {headers} body {json}")
        try:
            response = requests.post(
                f"{SUPABASE_URL}/auth/v1/token?grant_type=refresh_token",
                headers=headers,
                json=json,
                timeout=10,
            )
            logger.debug(f"Revalidate response headers: {response.headers}\nbody: {response.text}")  # DEBUG: response may contain tokens, dev use only
            response.raise_for_status()
        except requests.exceptions.Timeout as e:
            logger.error(f"Token refresh timed out: {e}")
            raise
        except requests.exceptions.ConnectionError as e:
            logger.error(f"Token refresh connection error: {e}")
            raise
        except requests.exceptions.HTTPError as e:
            logger.error(f"Token refresh failed with status {response.status_code}: {response.text}")
            raise
        except requests.exceptions.RequestException as e:
            logger.error(f"Token refresh request failed: {e}")
            raise

        try:
            data = response.json()
        except ValueError as e:
            logger.error(f"Token refresh response was not valid JSON: {e}")
            raise

        try:
            new_refresh_token = data["refresh_token"]
            new_access_token = data["access_token"]
        except KeyError as e:
            logger.error(f"Token refresh response missing expected field {e}")
            raise

        logger.info("Revalidated tokens")
        return new_refresh_token, new_access_token


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if value is None:
        return None
    return datetime.fromisoformat(value)


def _to_json(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return [_to_json(v) for v in value]
    if isinstance(value, dict):
        return {k: _to_json(v) for k, v in value.items()}
    return value


@dataclass
class User:
    id: str
    discord_username: str
    discord_id: Optional[int]
    member_number: int
    bio: Optional[str]
    favorite_champion: Optional[str]
    profile_title: Optional[str]
    username_changed_at: Optional[datetime]
    tournaments_joined:Optional[int]

    @classmethod
    def from_dict(cls, data: dict) -> "User":
        return cls(
            id=data["id"],
            discord_username=data["discord_username"],
            discord_id=int(data["discord_id"]) if data.get("discord_id") else None,
            member_number=data["member_number"],
            tournaments_joined=data["tournaments_joined"],
            bio=data.get("bio"),
            favorite_champion=data.get("favorite_champion"),
            profile_title=data.get("profile_title"),
            username_changed_at=_parse_dt(data.get("username_changed_at")),
        )

    def json(self) -> dict:
        return _to_json(asdict(self))


@dataclass
class Lobby:
    id: int
    lobby_number: int
    title: str
    lobby_type: str
    region: str
    match_size: int
    team_one_name: str
    team_two_name: str
    creator_id: str
    status: str
    draft_step: int
    draft_started_at: Optional[datetime]
    winner_slot: Optional[str]
    season_year: int
    season_number: int
    season_name: Optional[str]
    created_at: datetime
    ended_at: Optional[datetime]
    match_started_at: Optional[datetime]
    dispute_reason: Optional[str]
    winner_confirmed_by_team_one: Optional[bool]
    winner_confirmed_by_team_two: Optional[bool]
    result_team_one_vote: Optional[str]
    result_team_two_vote: Optional[str]
    discord_match_ready_requested_at: Optional[datetime]
    discord_match_ready_sent_at: Optional[datetime]
    discord_match_ready_status: Optional[str]
    discord_match_ready_error: Optional[str]
    mmr_applied: bool
    ban_count: int
    is_tournament: bool
    tournament_match_id: Optional[str]
    tournament_name: Optional[str]
    url_year: Optional[int]
    url_series: Optional[str]
    game_number: Optional[int]
    has_password: bool
    map_pool: Optional[List[str]]
    selected_map: Optional[str]
    team_one_roster: List[str] = field(default_factory=list)
    team_two_roster: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "Lobby":
        return cls(
            id=data["id"],
            lobby_number=data["lobby_number"],
            title=data["title"],
            lobby_type=data["lobby_type"],
            region=data["region"],
            match_size=data["match_size"],
            team_one_name=data["team_one_name"],
            team_two_name=data["team_two_name"],
            creator_id=data["creator_id"],
            status=data["status"],
            draft_step=data["draft_step"],
            draft_started_at=_parse_dt(data.get("draft_started_at")),
            winner_slot=data.get("winner_slot"),
            season_year=data["season_year"],
            season_number=data["season_number"],
            season_name=data.get("season_name"),
            created_at=_parse_dt(data["created_at"]),
            ended_at=_parse_dt(data.get("ended_at")),
            match_started_at=_parse_dt(data.get("match_started_at")),
            dispute_reason=data.get("dispute_reason"),
            winner_confirmed_by_team_one=data.get("winner_confirmed_by_team_one"),
            winner_confirmed_by_team_two=data.get("winner_confirmed_by_team_two"),
            result_team_one_vote=data.get("result_team_one_vote"),
            result_team_two_vote=data.get("result_team_two_vote"),
            discord_match_ready_requested_at=_parse_dt(data.get("discord_match_ready_requested_at")),
            discord_match_ready_sent_at=_parse_dt(data.get("discord_match_ready_sent_at")),
            discord_match_ready_status=data.get("discord_match_ready_status"),
            discord_match_ready_error=data.get("discord_match_ready_error"),
            mmr_applied=data["mmr_applied"],
            ban_count=data["ban_count"],
            is_tournament=data["is_tournament"],
            tournament_match_id=data.get("tournament_match_id"),
            tournament_name=data.get("tournament_name"),
            url_year=data.get("url_year"),
            url_series=data.get("url_series"),
            game_number=data.get("game_number"),
            has_password=data.get("has_password", False),
            map_pool=data.get("map_pool"),
            selected_map=data.get("selected_map"),
            team_one_roster=data.get("team_one_roster") or [],
            team_two_roster=data.get("team_two_roster") or [],
        )

    def json(self) -> dict:
        return _to_json(asdict(self))


@dataclass
class SeasonRating:
    id: int
    user_id: str
    season_year: int
    season_number: int
    mmr: int
    wins: int
    losses: int
    matches_played: int
    updated_at: datetime

    RANKS = [
        (8000, "BATTLEVIVE"),
        (5500, "Diamond"),
        (3500, "Platinum"),
        (2000, "Gold"),
        (1000, "Silver"),
        (0, "Bronze"),
    ]


    @classmethod
    def from_dict(cls, data: dict) -> "SeasonRating":
        return cls(
            id=data["id"],
            user_id=data["user_id"],
            season_year=data["season_year"],
            season_number=data["season_number"],
            mmr=data["mmr"],
            wins=data["wins"],
            losses=data["losses"],
            matches_played=data["matches_played"],
            updated_at=_parse_dt(data["updated_at"]),
        )


    
    @classmethod
    def rank(cls, mmr: int) -> str:
        for threshold, name in cls.RANKS:
            if mmr >= threshold:
                return name
        return "Bronze"


    def json(self):
        return _to_json(asdict(self))

# Dont use wait for upstream schema
@dataclass
class UserTrophy:
    data: dict

    @classmethod
    def from_dict(cls, data: dict) -> "UserTrophy":
        return cls(data=data)

    def json(self):
        return _to_json(self.data)


def parse_users(json_data) -> List[User]:
    import json as _json
    data = _json.loads(json_data) if isinstance(json_data, str) else json_data
    return [User.from_dict(item) for item in data]


def parse_lobbies(json_data) -> List[Lobby]:
    import json as _json
    data = _json.loads(json_data) if isinstance(json_data, str) else json_data
    return [Lobby.from_dict(item) for item in data]


def parse_season_ratings(json_data) -> List[SeasonRating]:
    import json as _json

    data = _json.loads(json_data) if isinstance(json_data, str) else json_data
    return [SeasonRating.from_dict(item) for item in data]

# Dont use wait for upstream schema
def parse_user_trophies(json_data) -> List[UserTrophy]:
    import json as _json

    data = _json.loads(json_data) if isinstance(json_data, str) else json_data
    return [UserTrophy.from_dict(item) for item in data]


async def _fetch_and_parse(session: aiohttp.ClientSession, JWT_token: str, endpoint: str, parser):
    headers = {
        "Authorization": f"Bearer {JWT_token}",
        "apikey": SUPABASE_API_KEY,
        "Content-Type": "application/json",
    }
    url = f"{SUPABASE_URL}/rest/v1/{endpoint}"

    logger.debug(f"Fetching {endpoint} from {url}")
    start = time.perf_counter()

    try:
        async with session.get(url, headers=headers) as response:
            if response.status != 200:
                body = await response.text()
                logger.error(f"{endpoint} request failed: status={response.status} body={body}")
                response.raise_for_status()
            raw = await response.json()
    except aiohttp.ClientError as e:
        logger.error(f"{endpoint} request error: {e}")
        raise

    try:
        result = parser(raw)
    except (KeyError, TypeError, ValueError) as e:
        logger.error(f"Failed to parse {endpoint} response: {e}")
        raise

    elapsed = time.perf_counter() - start
    logger.info(f"Fetched {len(result)} {endpoint} in {elapsed:.2f}s")
    return result

async def query_users(JWT_token: str) -> List[User]:
    async with aiohttp.ClientSession() as session:
        return await _fetch_and_parse(session, JWT_token, "users", parse_users)


async def query_lobbies(JWT_token: str) -> List[Lobby]:
    async with aiohttp.ClientSession() as session:
        return await _fetch_and_parse(session, JWT_token, "lobbies", parse_lobbies)


async def query_season_ratings(JWT_token: str) -> List[SeasonRating]:
    async with aiohttp.ClientSession() as session:
        return await _fetch_and_parse(
            session,
            JWT_token,
            "season_ratings",
            parse_season_ratings,
        )


async def sync_battlevive_data_to_db(
    users: List[User],
    lobbies: List[Lobby],
    season_ratings: List[SeasonRating],
) -> None:
    """
    Writes fetched data to Postgres in dependency order: users first, since
    lobbies.creator_id / lobby_rosters.user_id / season_ratings.user_id all
    foreign-key into users. Fetching can safely stay concurrent (network I/O,
    order doesn't matter); this write order is what actually prevents the
    ForeignKeyViolationError when a lobby references a brand-new user.
    """
    logger.info("Syncing local db with upstream")
    await sync_users_to_db(users)
    await sync_lobbies_to_db(lobbies)
    await sync_season_ratings_to_db(season_ratings)


async def sync_users_to_db(users: List[User]) -> None:
    if not users:
        return

    from db import get_pool
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
                uuid.UUID(u.id),
                u.discord_username,
                u.member_number,
                u.tournaments_joined,
                u.bio,
                u.favorite_champion,
                u.profile_title,
                u.username_changed_at,
            )
            for u in users
        ],
    )


async def sync_lobbies_to_db(lobbies: List[Lobby]) -> None:
    if not lobbies:
        return

    from db import get_pool
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
                        l.id, l.lobby_number, l.title, l.lobby_type, l.region, l.match_size,
                        l.team_one_name, l.team_two_name,
                        uuid.UUID(l.creator_id) if l.creator_id else None,
                        l.status,
                        l.draft_step, l.draft_started_at, l.winner_slot,
                        l.season_year, l.season_number, l.season_name,
                        l.created_at, l.ended_at, l.match_started_at,
                        l.dispute_reason, l.winner_confirmed_by_team_one, l.winner_confirmed_by_team_two,
                        l.result_team_one_vote, l.result_team_two_vote,
                        l.discord_match_ready_requested_at, l.discord_match_ready_sent_at,
                        l.discord_match_ready_status, l.discord_match_ready_error,
                        l.mmr_applied, l.ban_count, l.is_tournament, l.tournament_match_id, l.tournament_name,
                        l.url_year, l.url_series, l.game_number, l.has_password, l.map_pool, l.selected_map,
                    )
                    for l in lobbies
                ],
            )

            lobby_ids = [l.id for l in lobbies]
            await conn.execute(
                "DELETE FROM lobby_rosters WHERE lobby_id = ANY($1::int[])",
                lobby_ids,
            )

            roster_rows = [
                (l.id, uuid.UUID(uid), "team_one")
                for l in lobbies
                for uid in l.team_one_roster
            ] + [
                (l.id, uuid.UUID(uid), "team_two")
                for l in lobbies
                for uid in l.team_two_roster
            ]
            if roster_rows:
                await conn.executemany(
                    "INSERT INTO lobby_rosters (lobby_id, user_id, team) VALUES ($1, $2, $3)",
                    roster_rows,
                )


async def sync_season_ratings_to_db(ratings: List[SeasonRating]) -> None:
    if not ratings:
        return

    from db import get_pool
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
                r.id,
                uuid.UUID(r.user_id),
                r.season_year,
                r.season_number,
                r.mmr,
                r.wins,
                r.losses,
                r.matches_played,
                r.updated_at,
            )
            for r in ratings
        ],
    )

# Dont use wait for upstream schema
async def query_user_trophies(JWT_token: str) -> List[UserTrophy]:
    async with aiohttp.ClientSession() as session:
        return await _fetch_and_parse(
            session,
            JWT_token,
            "user_trophies",
            parse_user_trophies,
        )

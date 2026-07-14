#!/usr/bin/env python3
# pyrefly: ignore [missing-import]
from dotenv import load_dotenv
# pyrefly: ignore [missing-import]
import aiohttp
import os,requests,json,time
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


#website api is bloated so classes are bloated most of the fields are obsolite TO DO debloat classes leave only important stuff. 
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
    battlerite_password: Optional[str]
    draft_step: int
    draft_started_at: Optional[datetime]
    winner_slot: Optional[str]
    season_year: int
    season_number: int
    created_at: datetime
    ended_at: Optional[datetime]
    winner_confirmed_by_team_one: Optional[bool]
    winner_confirmed_by_team_two: Optional[bool]
    match_started_at: Optional[datetime]
    match_duration_seconds: Optional[int]
    dispute_reason: Optional[str]
    battlerite_lobby_code: Optional[str]
    battlerite_lobby_password: Optional[str]
    battlerite_host_user_id: Optional[str]
    battlerite_steam_url: Optional[str]
    battlerite_join_message: Optional[str]
    battlerite_countdown_seconds: int
    discord_match_ready_requested_at: Optional[datetime]
    discord_match_ready_sent_at: Optional[datetime]
    discord_match_ready_status: Optional[str]
    creator_name: str
    battlerite_steam_lobby_id: Optional[str]
    battlerite_host_steam_url: Optional[str]
    battlerite_join_steam_url: Optional[str]
    discord_match_ready_error: Optional[str]
    lobby_password: Optional[str]
    season_name: Optional[str]
    tournament_name: Optional[str]
    result_team_one_vote: Optional[str]
    result_team_two_vote: Optional[str]
    mmr_applied: bool
    obsolete_at: Optional[datetime]
    obsolete_by: Optional[str]
    updated_at: Optional[datetime]
    ban_count: int
    is_tournament: bool
    tournament_match_id: Optional[str]
    team_one_roster: List[str] = field(default_factory=list)
    team_two_roster: List[str] = field(default_factory=list)
    url_year: Optional[int] = None
    url_series: Optional[str] = None
    game_number: Optional[int] = None
    has_password: bool = False
    view_count: int = 0
    rematch_child_id: Optional[int] = None
    map_pool: Optional[List[str]] = None
    selected_map: Optional[str] = None

    @staticmethod
    def _parse_dt(value: Optional[str]) -> Optional[datetime]:
        if value is None:
            return None
        return datetime.fromisoformat(value)


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
            battlerite_password=data.get("battlerite_password"),
            draft_step=data["draft_step"],
            draft_started_at=cls._parse_dt(data.get("draft_started_at")),
            winner_slot=data.get("winner_slot"),
            season_year=data["season_year"],
            season_number=data["season_number"],
            created_at=cls._parse_dt(data["created_at"]),
            ended_at=cls._parse_dt(data.get("ended_at")),
            winner_confirmed_by_team_one=data.get("winner_confirmed_by_team_one"),
            winner_confirmed_by_team_two=data.get("winner_confirmed_by_team_two"),
            match_started_at=cls._parse_dt(data.get("match_started_at")),
            match_duration_seconds=data.get("match_duration_seconds"),
            dispute_reason=data.get("dispute_reason"),
            battlerite_lobby_code=data.get("battlerite_lobby_code"),
            battlerite_lobby_password=data.get("battlerite_lobby_password"),
            battlerite_host_user_id=data.get("battlerite_host_user_id"),
            battlerite_steam_url=data.get("battlerite_steam_url"),
            battlerite_join_message=data.get("battlerite_join_message"),
            battlerite_countdown_seconds=data["battlerite_countdown_seconds"],
            discord_match_ready_requested_at=cls._parse_dt(data.get("discord_match_ready_requested_at")),
            discord_match_ready_sent_at=cls._parse_dt(data.get("discord_match_ready_sent_at")),
            discord_match_ready_status=data.get("discord_match_ready_status"),
            creator_name=data["creator_name"],
            battlerite_steam_lobby_id=data.get("battlerite_steam_lobby_id"),
            battlerite_host_steam_url=data.get("battlerite_host_steam_url"),
            battlerite_join_steam_url=data.get("battlerite_join_steam_url"),
            discord_match_ready_error=data.get("discord_match_ready_error"),
            lobby_password=data.get("lobby_password"),
            season_name=data.get("season_name"),
            tournament_name=data.get("tournament_name"),
            result_team_one_vote=data.get("result_team_one_vote"),
            result_team_two_vote=data.get("result_team_two_vote"),
            mmr_applied=data["mmr_applied"],
            obsolete_at=cls._parse_dt(data.get("obsolete_at")),
            obsolete_by=data.get("obsolete_by"),
            updated_at=cls._parse_dt(data.get("updated_at")),
            ban_count=data["ban_count"],
            is_tournament=data["is_tournament"],
            tournament_match_id=data.get("tournament_match_id"),
            team_one_roster=data.get("team_one_roster") or [],
            team_two_roster=data.get("team_two_roster") or [],
            url_year=data.get("url_year"),
            url_series=data.get("url_series"),
            game_number=data.get("game_number"),
            has_password=data.get("has_password", False),
            view_count=data.get("view_count", 0),
            rematch_child_id=data.get("rematch_child_id"),
            map_pool=data.get("map_pool"),
            selected_map=data.get("selected_map"),
        )
    def json(self) -> dict:
        def convert(value):
            if isinstance(value, datetime):
                return value.isoformat()
            if isinstance(value, list):
                return [convert(v) for v in value]
            if isinstance(value, dict):
                return {k: convert(v) for k, v in value.items()}
            return value

        return convert(asdict(self))


@dataclass
class User:
    id: str
    discord_username: str
    discord_avatar: Optional[str]
    member_number: int
    role: str
    created_at: datetime
    discord_id: Optional[str]
    matches_played: int
    wins: int
    losses: int
    tournaments_joined: int
    trophies: int
    bio: Optional[str]
    banner_url: Optional[str]
    favorite_champion: Optional[str]
    profile_title: Optional[str]
    updated_at: Optional[datetime]
    last_seen_at: Optional[datetime]
    current_mmr: int
    peak_mmr: int
    total_mmr_delta: int
    replay_count: int
    username_changed_at: Optional[datetime]
 

    #ranks list used by rank method
    RANKS = [
        (8000, "BATTLEVIVE"),
        (5500, "Diamond"),
        (3500, "Platinum"),
        (2000, "Gold"),
        (1000, "Silver"),
        (0, "Bronze"),
    ]


    @staticmethod
    def _parse_dt(value: Optional[str]) -> Optional[datetime]:
        if value is None:
            return None
        return datetime.fromisoformat(value)


    @classmethod
    def from_dict(cls, data: dict) -> "User":
        return cls(
            id=data["id"],
            discord_username=data["discord_username"],
            discord_avatar=data.get("discord_avatar"),
            member_number=data["member_number"],
            role=data["role"],
            created_at=cls._parse_dt(data["created_at"]),
            discord_id=data.get("discord_id"),
            matches_played=data["matches_played"],
            wins=data["wins"],
            losses=data["losses"],
            tournaments_joined=data["tournaments_joined"],
            trophies=data["trophies"],
            bio=data.get("bio"),
            banner_url=data.get("banner_url"),
            favorite_champion=data.get("favorite_champion"),
            profile_title=data.get("profile_title"),
            updated_at=cls._parse_dt(data.get("updated_at")),
            last_seen_at=cls._parse_dt(data.get("last_seen_at")),
            current_mmr=data["current_mmr"],
            peak_mmr=data["peak_mmr"],
            total_mmr_delta=data["total_mmr_delta"],
            replay_count=data["replay_count"],
            username_changed_at=cls._parse_dt(data.get("username_changed_at")),
        )


    def rank(self, peek=False):
        mmr = self.peak_mmr if peek else self.current_mmr
        for threshold, name in self.RANKS:
            if mmr >= threshold:
                return name
        
    def json(self) -> dict:
        def convert(value):
            if isinstance(value, datetime):
                return value.isoformat()
            if isinstance(value, list):
                return [convert(v) for v in value]
            if isinstance(value, dict):
                return {k: convert(v) for k, v in value.items()}
            return value

        return convert(asdict(self))

 
def parse_users(json_data) -> List[User]:
    """
    Accepts either a JSON string or an already-parsed list of dicts.
    Returns a list of User objects.
    """
    if isinstance(json_data, str):
        data = json.loads(json_data)
    else:
        data = json_data
 
    return [User.from_dict(item) for item in data]


def parse_lobbies(json_data) -> List[Lobby]:
    """
    Accepts either a JSON string or an already-parsed list of dicts.
    Returns a list of Lobby objects.
    """
    if isinstance(json_data, str):
        data = json.loads(json_data)
    else:
        data = json_data

    return [Lobby.from_dict(item) for item in data]


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



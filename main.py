#!/usr/bin/env python3
# pyrefly: ignore [missing-import]
import discord
# pyrefly: ignore [missing-import]
from discord.ext import tasks,commands
import logging
import time
# pyrefly: ignore [missing-import]
from dotenv import load_dotenv
import os
import requests 
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List
import json
import asyncio
# pyrefly: ignore [missing-import]
import aiohttp
from dataclasses import dataclass, field

#logs stuff
os.makedirs('logs', exist_ok=True)

formatter = logging.Formatter('%(asctime)s %(levelname)-8s %(name)s: %(message)s')

bot_handler = logging.FileHandler(filename='logs/bot.log', encoding='utf-8', mode='a')
bot_handler.setFormatter(formatter)

discord_handler = logging.FileHandler(filename='logs/discord.log', encoding='utf-8', mode='a')
discord_handler.setFormatter(formatter)

logger = logging.getLogger("bot")  # your own namespace for calls like logger.info(...)
logger.setLevel(logging.INFO)
logger.addHandler(bot_handler)

discord_logger = logging.getLogger("discord")  # library's namespace
discord_logger.setLevel(logging.ERROR)
discord_logger.addHandler(discord_handler)

#getting env variables(all besides bootstrap keys, they are inline with token objects)
load_dotenv()
SUPABASE_API_KEY = os.getenv('SUPABASE_API_KEY')
SUPABASE_URL = os.getenv('SUPABASE_URL')
DISCORD_BOT_TOKEN = os.getenv('DISCORD_TOKEN')

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
        response = requests.post(f"{SUPABASE_URL}/auth/v1/token?grant_type=refresh_token", headers=headers, json=json)
        data=response.json()
        new_refresh_token =data["refresh_token"] 
        new_JWT= data["access_token"] 

        return new_refresh_token,new_JWT  


battlevive_tokens= BattlevivieTokenManager(JWT_token=os.getenv('BOOTSTRAP_JWT'),refresh_token=os.getenv('BOOTSTRAP_REFRESH_TOKEN'))
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

    result = parser(raw)
    elapsed = time.perf_counter() - start
    logger.info(f"Fetched {len(result)} {endpoint} in {elapsed:.2f}s")
    return result


async def query_users(JWT_token: str) -> List[User]:
    async with aiohttp.ClientSession() as session:
        return await _fetch_and_parse(session, JWT_token, "users", parse_users)

async def query_lobbies(JWT_token: str) -> List[Lobby]:
    async with aiohttp.ClientSession() as session:
        return await _fetch_and_parse(session, JWT_token, "lobbies", parse_lobbies)

async def refresh_all_data():
    results = await asyncio.gather(
        query_users(battlevive_tokens.JWT_token),
        query_lobbies(battlevive_tokens.JWT_token),
        return_exceptions=True,
    )
    return results

@tasks.loop(minutes=30)
async def revalidate_tokens():
    new_refresh_token, new_JWT = BattlevivieTokenManager.revalidate(refresh_token=battlevive_tokens.refresh_token)
    battlevive_tokens.JWT_token= new_JWT
    battlevive_tokens.refresh_token = new_refresh_token

@tasks.loop(minutes=1)
async def refresh_loop():
    await refresh_all_data()

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!bt", intents=intents, strip_after_prefix=True)

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    logger.info(f"Received message: '{message.content}' from {message.author}")
    await bot.process_commands(message)
@bot.event
async def setup_hook():
    revalidate_tokens.start()
    refresh_loop.start()
    #await bot.tree.sync()  # <-- registers slash commands with Discord
    bot.tree.copy_global_to(guild=discord.Object(id=1524804820098224240))
    await bot.tree.sync(guild=discord.Object(id=1524804820098224240)) # for prod change to global sync 


@bot.tree.command(name="ping", description="Check bot latency")
async def ping_slash(interaction: discord.Interaction):
    await interaction.response.send_message(f"Pong! {round(bot.latency * 1000)}ms")


@bot.command(name="ping")  # <-- separate, needed for !bt ping
async def ping_prefix(ctx):
    await ctx.send(f"Pong! {round(bot.latency * 1000)}ms")


bot.run(DISCORD_BOT_TOKEN, log_handler=None)  # must be last
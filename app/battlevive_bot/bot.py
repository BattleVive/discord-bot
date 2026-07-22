from __future__ import annotations

import asyncio
from io import BytesIO
import json
import os

# pyrefly: ignore [missing-import]
import discord
# pyrefly: ignore [missing-import]
from discord import app_commands
# pyrefly: ignore [missing-import]
from discord.ext import commands
# pyrefly: ignore [missing-import]
from discord.ext import tasks
# pyrefly: ignore [missing-import]
from PIL import Image
import requests

from . import db
from .battlevive_api import BattleviveTokenManager
from .battlevive_api import query_lobbies
from .battlevive_api import query_season_ratings
from .battlevive_api import query_users
from .db import close_pool
from .db import get_pool
from .db import init_pool
from .db import sync_battlevive_data_to_db
from .images import build_card
from .logs import logger
from .models import Lobby
from .models import SeasonRating
from .models import User
from .roles import create_roles
from .roles import give_battlevive_role
from .roles import give_rank_roles
from .settings import BATTLEVIVE_BOOTSTRAP_JWT
from .settings import BATTLEVIVE_BOOTSTRAP_REFRESH_TOKEN
from .settings import COMMAND_SYNC_GUILD_ID
from .settings import DATABASE_URL
from .settings import DATA_DIR
from .settings import DISCORD_BOT_TOKEN


DATA_DIR.mkdir(exist_ok=True)

battlevive_tokens = BattleviveTokenManager(
    JWT_token=BATTLEVIVE_BOOTSTRAP_JWT,
    refresh_token=BATTLEVIVE_BOOTSTRAP_REFRESH_TOKEN,
)

battlevive_users: list[User] = []
lobbies: list[Lobby] = []
season_ratings: list[SeasonRating] = []


# Data refresh and background loops
async def refresh_all_data() -> tuple[list[User], list[Lobby], list[SeasonRating]]:
    results = await asyncio.gather(
        query_users(battlevive_tokens.JWT_token),
        query_lobbies(battlevive_tokens.JWT_token),
        query_season_ratings(battlevive_tokens.JWT_token),
        return_exceptions=True,
    )
    for result in results:
        if isinstance(result, Exception):
            raise result

    users, fetched_lobbies, fetched_season_ratings = results

    await sync_battlevive_data_to_db(users, fetched_lobbies, fetched_season_ratings)
    return users, fetched_lobbies, fetched_season_ratings


@tasks.loop(minutes=30)
async def revalidate_tokens() -> None:
    try:
        new_refresh_token, new_JWT = BattleviveTokenManager.revalidate(
            refresh_token=battlevive_tokens.refresh_token
        )
    except requests.exceptions.RequestException as error:
        logger.error("Failed to revalidate tokens: %s", error)
        raise

    battlevive_tokens.JWT_token = new_JWT
    battlevive_tokens.refresh_token = new_refresh_token
    logger.debug("Token revalidation cycle completed successfully.")


@revalidate_tokens.error
async def revalidate_tokens_error(error: Exception) -> None:
    logger.error("revalidate_tokens loop stopped due to unhandled exception: %s", error)


@tasks.loop(minutes=1)
async def refresh_loop() -> None:
    global battlevive_users
    global lobbies
    global season_ratings

    try:
        battlevive_users, lobbies, season_ratings = await refresh_all_data()
    except Exception:
        logger.exception("Failed to refresh Battlevive data, skipping this cycle.")
        return

    logger.debug(
        "Refreshed %d users and %d lobbies.",
        len(battlevive_users),
        len(lobbies),
    )

    try:
        battlevive_users = await give_battlevive_role(bot, battlevive_tokens)
        battlevive_users, season_ratings = await give_rank_roles(bot, battlevive_tokens)
    except Exception:
        logger.exception("Failed to sync roles this cycle.")


@refresh_loop.error
async def refresh_loop_error(error: Exception) -> None:
    logger.error("refresh_loop stopped due to unhandled exception: %s", error)


intents = discord.Intents.default()
intents.message_content = True
intents.members = True

# Bot setup
bot = commands.Bot(command_prefix="!bt", intents=intents, strip_after_prefix=True)


# Events
@bot.event
async def on_message(message: discord.Message) -> None:
    if message.author == bot.user:
        return

    logger.debug("Received message: '%s' from %s", message.content, message.author)
    await bot.process_commands(message)


@bot.event
async def setup_hook() -> None:
    await init_pool(DATABASE_URL)
    revalidate_tokens.start()
    refresh_loop.start()

    guild = discord.Object(id=COMMAND_SYNC_GUILD_ID)
    bot.tree.copy_global_to(guild=guild)
    await bot.tree.sync(guild=guild)


@bot.event
async def on_close() -> None:
    await close_pool()


# Commands

# Discord configuration commands
config_group = app_commands.Group(
    name="config",
    description="Configure Battlevive bot settings for this server",
)
config_leaderboard_group = app_commands.Group(
    name="leaderboard",
    description="Configure leaderboard settings",
    parent=config_group,
)
config_reset_group = app_commands.Group(
    name="reset",
    description="Reset Battlevive bot settings",
    parent=config_group,
)


async def _check_config_access(interaction: discord.Interaction) -> bool:
    if interaction.guild is None:
        await interaction.response.send_message(
            "This command can only be used in a server.",
            ephemeral=True,
        )
        return False

    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message(
            "You need the Manage Server permission to change bot configuration.",
            ephemeral=True,
        )
        return False

    return True


def _config_channel_permissions(
    channel: discord.abc.GuildChannel,
    guild: discord.Guild,
) -> bool:
    bot_member = guild.me
    if bot_member is None:
        return False

    permissions = channel.permissions_for(bot_member)
    return (
        permissions.view_channel
        and permissions.send_messages
        and permissions.embed_links
    )


async def _send_config_failure(
    interaction: discord.Interaction,
    message: str,
) -> None:
    logger.exception(message)
    if not interaction.response.is_done():
        await interaction.response.send_message(
            "Command failed. Check bot.log.",
            ephemeral=True,
        )


@config_leaderboard_group.command(
    name="channel",
    description="Set the channel for future leaderboard posts",
)
@app_commands.describe(channel="Text or news channel for leaderboard posts")
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
async def config_leaderboard_channel(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
) -> None:
    if not await _check_config_access(interaction):
        return

    if channel.type not in (discord.ChannelType.text, discord.ChannelType.news):
        await interaction.response.send_message(
            "Please choose a text or news channel.",
            ephemeral=True,
        )
        return

    if not _config_channel_permissions(channel, interaction.guild):
        await interaction.response.send_message(
            "I need View Channel, Send Messages, and Embed Links "
            "permissions in that channel.",
            ephemeral=True,
        )
        return

    try:
        await db.upsert_guild_config(
            interaction.guild.id,
            channel.id,
            interaction.user.id,
        )
        await interaction.response.send_message(
            f"Leaderboard channel set to {channel}.",
            ephemeral=True,
        )
    except Exception:
        await _send_config_failure(interaction, "config leaderboard channel failed")


@config_reset_group.command(
    name="leaderboard",
    description="Reset the leaderboard configuration",
)
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
async def config_reset_leaderboard(interaction: discord.Interaction) -> None:
    if not await _check_config_access(interaction):
        return

    try:
        await db.reset_guild_config(interaction.guild.id, interaction.user.id)
        await interaction.response.send_message(
            "Leaderboard configuration reset.",
            ephemeral=True,
        )
    except Exception:
        await _send_config_failure(interaction, "config reset leaderboard failed")


@config_group.command(name="show", description="Show this server's bot configuration")
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
async def config_show(interaction: discord.Interaction) -> None:
    if not await _check_config_access(interaction):
        return

    try:
        config = await db.get_guild_config(interaction.guild.id)
        channel_id = config["leaderboard_channel_id"] if config else None
        message = (
            f"Leaderboard channel: <#{channel_id}>."
            if channel_id is not None
            else "Leaderboard channel: not configured."
        )
        await interaction.response.send_message(message, ephemeral=True)
    except Exception:
        await _send_config_failure(interaction, "config show failed")


bot.tree.add_command(config_group)


@bot.tree.command(name="create_roles", description="Create required roles")
async def create_roles_slash(interaction: discord.Interaction) -> None:
    logger.info(
        "create_roles called by %s in guild '%s' (%s)",
        interaction.user,
        interaction.guild.name,
        interaction.guild.id,
    )
    try:
        await create_roles(interaction.guild)
        await interaction.response.send_message("Created roles")
    except Exception:
        logger.exception(
            "create_roles command failed for guild '%s' (%s)",
            interaction.guild.name,
            interaction.guild.id,
        )
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "Command failed. Check bot.log.",
                ephemeral=True,
            )


@bot.tree.command(
    name="debug_get_db_data",
    description="Dump all data from db unformatted",
)
async def debug_get_db_data(interaction: discord.Interaction) -> None:
    try:
        logger.info("debug_get_battlevive_data called by %s", interaction.user)

        users_file = DATA_DIR / "users.json"
        lobbies_file = DATA_DIR / "lobbies.json"
        ratings_file = DATA_DIR / "ratings.json"

        db_lobbies = await db.get_lobbies()
        db_users = await db.get_users()
        db_ratings = await db.get_season_ratings()

        logger.debug(
            "Dumping %d users and %d lobbies and ratings %d.",
            len(battlevive_users),
            len(db_lobbies),
            len(season_ratings),
        )
        with users_file.open("w", encoding="utf-8") as file:
            json.dump([user.json() for user in db_users], file, indent=2)

        with lobbies_file.open("w", encoding="utf-8") as file:
            json.dump([lobby.json() for lobby in db_lobbies], file, indent=2)

        with ratings_file.open("w", encoding="utf-8") as file:
            json.dump([rating.json() for rating in db_ratings], file, indent=2)

        await interaction.response.send_message(
            files=[
                discord.File(str(users_file)),
                discord.File(str(lobbies_file)),
                discord.File(str(ratings_file)),
            ],
            ephemeral=True,
        )
        os.remove(users_file)
        os.remove(lobbies_file)
        os.remove(ratings_file)

    except Exception:
        logger.exception("debug_get_battlevive_data failed")

        if not interaction.response.is_done():
            await interaction.response.send_message(
                "Command failed. Check bot.log.",
                ephemeral=True,
            )


@bot.tree.command(
    name="debug_get_battlevive_data",
    description="Dump all data from battlevive unformatted",
)
async def debug_get_battlevive_data(interaction: discord.Interaction) -> None:
    try:
        logger.info("debug_get_battlevive_data called by %s", interaction.user)

        logger.debug(
            "Dumping %d users and %d lobbies and ratings %d.",
            len(battlevive_users),
            len(lobbies),
            len(season_ratings),
        )
        users_file = DATA_DIR / "users.json"
        lobbies_file = DATA_DIR / "lobbies.json"
        ratings_file = DATA_DIR / "ratings.json"

        with users_file.open("w", encoding="utf-8") as file:
            json.dump([user.json() for user in battlevive_users], file, indent=2)

        with lobbies_file.open("w", encoding="utf-8") as file:
            json.dump([lobby.json() for lobby in lobbies], file, indent=2)

        with ratings_file.open("w", encoding="utf-8") as file:
            json.dump([rating.json() for rating in season_ratings], file, indent=2)

        await interaction.response.send_message(
            files=[
                discord.File(str(users_file)),
                discord.File(str(lobbies_file)),
                discord.File(str(ratings_file)),
            ],
            ephemeral=True,
        )
    except Exception:
        logger.exception("debug_get_battlevive_data failed")

        if not interaction.response.is_done():
            await interaction.response.send_message(
                "Command failed. Check bot.log.",
                ephemeral=True,
            )


@bot.tree.command(
    name="rank",
    description="Display your rank",
)
async def rank_command(interaction: discord.Interaction) -> None:
    logger.info("rank called by %s (%s)", interaction.user, interaction.user.id)
    try:
        pool = get_pool()

        user = await pool.fetchrow(
            """
            SELECT
                users.discord_id,
                users.discord_username,
                users.member_number,
                season_ratings.mmr,
                season_ratings.wins,
                season_ratings.losses
            FROM users
            INNER JOIN season_ratings ON users.id = season_ratings.user_id
            WHERE users.discord_id = $1
            """,
            interaction.user.id,
        )

        if user is None:
            logger.debug("rank: no DB record for discord_id=%s", interaction.user.id)
            await interaction.response.send_message(
                "You are not registered.",
                ephemeral=True,
            )
            return

        mmr = user["mmr"]
        rank_current = SeasonRating.rank(mmr)

        rank_next = rank_current
        mmr_required = mmr
        for index, (threshold, name) in enumerate(SeasonRating.RANKS):
            if mmr >= threshold:
                if index > 0:
                    mmr_required, rank_next = SeasonRating.RANKS[index - 1]
                break

        logger.debug(
            "rank: user=%s mmr=%d current=%s next=%s mmr_required=%d",
            interaction.user.id,
            mmr,
            rank_current,
            rank_next,
            mmr_required,
        )

        avatar_bytes = await interaction.user.display_avatar.with_size(128).read()
        avatar = Image.open(BytesIO(avatar_bytes))

        png = build_card(
            avatar=avatar,
            display_name=interaction.user.display_name,
            rank_current=rank_current,
            rank_next=rank_next,
            mmr_current=mmr,
            mmr_required=mmr_required,
            wins=user["wins"],
            losses=user["losses"],
        )
        file = discord.File(BytesIO(png), filename="profile.png")
        await interaction.response.send_message(file=file)
        logger.info(
            "rank: sent profile card to %s (%s)",
            interaction.user,
            interaction.user.id,
        )
    except Exception:
        logger.exception(
            "rank command failed for %s (%s)",
            interaction.user,
            interaction.user.id,
        )
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "Command failed. Check bot.log.",
                ephemeral=True,
            )


# Runtime
def run() -> None:
    bot.run(DISCORD_BOT_TOKEN, log_handler=None)

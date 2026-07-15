#!/usr/bin/env python3
# pyrefly: ignore [missing-import]
import discord
# pyrefly: ignore [missing-import]
from discord import app_commands
# pyrefly: ignore [missing-import]
from discord.ext import tasks,commands
# pyrefly: ignore [missing-import]
from dotenv import load_dotenv
import os,asyncio,requests
from dataclasses import dataclass

import json
from battlevive import BattlevivieTokenManager,User,Lobby,query_lobbies,query_users
from logs import discord_logger,logger

#creating dirs if  not present
DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)


#getting env variables(all besides bootstrap keys, they are inline with token objects)
load_dotenv()
DISCORD_BOT_TOKEN = os.getenv("DISCORD_TOKEN")
battlevive_tokens= BattlevivieTokenManager(JWT_token=os.getenv("BOOTSTRAP_JWT"),refresh_token=os.getenv("BOOTSTRAP_REFRESH_TOKEN"))

battlevive_users: list[User] = []
lobbies: list[Lobby] = []

async def refresh_all_data():
    results = await asyncio.gather(
        query_users(battlevive_tokens.JWT_token),
        query_lobbies(battlevive_tokens.JWT_token),
        return_exceptions=True,
    )
    for result in results:
        if isinstance(result, Exception):
            raise result
    return results


async def create_roles(guild: discord.Guild):
    logger.info("Creating Battlevive roles in guild '%s' (%s)", guild.name, guild.id)

    for _, rank_name in User.RANKS:
        existing_role = discord.utils.get(guild.roles, name=rank_name)

        if existing_role is not None:
            logger.debug(
                "Role '%s' already exists in guild '%s' (%s), skipping.",
                rank_name,
                guild.name,
                guild.id,
            )
            continue

        try:
            role = await guild.create_role(name=rank_name)
            logger.info(
                "Created role '%s' (%s) in guild '%s' (%s).",
                role.name,
                role.id,
                guild.name,
                guild.id,
            )
        except discord.Forbidden:
            logger.exception(
                "Missing permissions to create role '%s' in guild '%s' (%s).",
                rank_name,
                guild.name,
                guild.id,
            )
        except discord.HTTPException:
            logger.exception(
                "Failed to create role '%s' in guild '%s' (%s).",
                rank_name,
                guild.name,
                guild.id,
            )

    role_name = "Battlevive Player"
    existing_role = discord.utils.get(guild.roles, name=role_name)

    if existing_role is not None:
        logger.debug(
            "Role '%s' already exists in guild '%s' (%s), skipping.",
            role_name,
            guild.name,
            guild.id,
        )
        return

    try:
        role = await guild.create_role(name=role_name)
        logger.info(
            "Created role '%s' (%s) in guild '%s' (%s).",
            role.name,
            role.id,
            guild.name,
            guild.id,
        )
    except discord.Forbidden:
        logger.exception(
            "Missing permissions to create role '%s' in guild '%s' (%s).",
            role_name,
            guild.name,
            guild.id,
        )
    except discord.HTTPException:
        logger.exception(
            "Failed to create role '%s' in guild '%s' (%s).",
            role_name,
            guild.name,
            guild.id,
        )   

async def give_battlevive_role():
    rank_name = "Battlevive Player"
    for guild in bot.guilds:
        role = discord.utils.get(guild.roles, name=rank_name)
        if role is None:
            continue
        for user in battlevive_users:
            member = None
            if user.discord_id is not None:
                member = guild.get_member(user.discord_id)
                if member is None:
                    try:
                        member = await guild.fetch_member(user.discord_id)
                    except discord.NotFound:
                        continue  # user not in this guild, skip
                    except discord.HTTPException:
                        continue  # other API error, skip
                    except discord.Forbidden:
                        continue
            else:
                results = await guild.query_members(query=user.discord_username)
                member = discord.utils.get(results, display_name=user.discord_username)

            if member is None:
                logger.debug("No member found for %s in guild '%s'", user.discord_username, guild.name)
                continue

            logger.debug(f"id: {member.id} username: {member.name}")

            if role not in member.roles:
                try:
                    await member.add_roles(role)
                    logger.debug(
                        "Gave role '%s' to %s in guild '%s' (%s).",
                        role.name, member, guild.name, guild.id,
                    )
                except (discord.Forbidden, discord.HTTPException):
                    logger.exception(
                        "Failed to give role '%s' to %s in guild '%s' (%s).",
                        role.name, member, guild.name, guild.id,
                    )


async def give_rank_roles():
    for guild in bot.guilds:
        for user in battlevive_users:
            member = None
            if user.discord_id is not None:
                member = guild.get_member(user.discord_id)
                if member is None:
                    try:
                        member = await guild.fetch_member(user.discord_id)
                    except (discord.NotFound, discord.HTTPException):
                        continue
            else:
                results = await guild.query_members(query=user.discord_username)
                member = discord.utils.get(results, display_name=user.discord_username)

            if member is None:
                logger.debug("No member found for %s in guild '%s'", user.discord_username, guild.name)
                continue

            logger.debug(f"id: {member.id} username: {member.name}")

            rank_name = user.rank()
            role = discord.utils.get(guild.roles, name=rank_name)
            if role is None:
                logger.debug(
                    "Rank role '%s' not found in guild '%s' (%s), skipping user %s.",
                    rank_name, guild.name, guild.id, member,
                )
                continue
            old_rank_roles = [
                r for r in member.roles
                if r.name in user.RANKS
            ]
            try:
                if old_rank_roles:
                    await member.remove_roles(*old_rank_roles)
                if role not in member.roles:
                    await member.add_roles(role)
                    logger.debug(
                        "Updated rank role for %s to '%s' in guild '%s' (%s).",
                        member, rank_name, guild.name, guild.id,
                    )
            except (discord.Forbidden, discord.HTTPException):
                logger.exception(
                    "Failed to update rank role for %s in guild '%s' (%s).",
                    member, guild.name, guild.id,
                )
                continue


@tasks.loop(minutes=30)
async def revalidate_tokens():
    try:
        new_refresh_token, new_JWT = BattlevivieTokenManager.revalidate(
            refresh_token=battlevive_tokens.refresh_token
        )
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to revalidate tokens: {e}")
        raise

    battlevive_tokens.JWT_token = new_JWT
    battlevive_tokens.refresh_token = new_refresh_token
    logger.debug("Token revalidation cycle completed successfully.")


@revalidate_tokens.error
async def revalidate_tokens_error(error: Exception):
    logger.error(f"revalidate_tokens loop stopped due to unhandled exception: {error}")


@tasks.loop(minutes=1)
async def refresh_loop():
    global battlevive_users, lobbies

    try:
        battlevive_users, lobbies = await refresh_all_data()
    except Exception:
        logger.exception("Failed to refresh Battlevive data, skipping this cycle.")
        return

    logger.debug("Refreshed %d users and %d lobbies.", len(battlevive_users), len(lobbies))

    try:
        await give_battlevive_role()
        await give_rank_roles()
    except Exception:
        logger.exception("Failed to sync roles this cycle.")


@refresh_loop.error
async def refresh_loop_error(error: Exception):
    logger.error(f"refresh_loop stopped due to unhandled exception: {error}")


intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!bt", intents=intents, strip_after_prefix=True)


@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    logger.debug(f"Received message: '{message.content}' from {message.author}")  # DEBUG: message content, dev use only
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
    logger.debug("ping called by %s", interaction.user)
    await interaction.response.send_message(f"Pong! {round(bot.latency * 1000)}ms")


@bot.tree.command(name="create_roles", description="Create required roles")
async def create_roles_slash(interaction: discord.Interaction):
    logger.info("create_roles called by %s in guild '%s' (%s)", interaction.user, interaction.guild.name, interaction.guild.id)
    try:
        await create_roles(interaction.guild)
        await interaction.response.send_message("Created roles")
    except Exception:
        logger.exception("create_roles command failed for guild '%s' (%s)", interaction.guild.name, interaction.guild.id)
        if not interaction.response.is_done():
            await interaction.response.send_message("Command failed. Check bot.log.", ephemeral=True)


@bot.tree.command(
    name="debug_get_battlevive_data",
    description="Dump all data from battlevive unformatted",
)
async def debug_get_battlevive_data(interaction: discord.Interaction):
    try:
        logger.info("debug_get_battlevive_data called by %s", interaction.user)

        logger.debug("Dumping %d users and %d lobbies.", len(battlevive_users), len(lobbies))

        users_file = os.path.join(DATA_DIR, "users.json")
        lobbies_file = os.path.join(DATA_DIR, "lobbies.json")

        with open(users_file, "w", encoding="utf-8") as f:
            json.dump([u.json() for u in battlevive_users], f, indent=2)

        with open(lobbies_file, "w", encoding="utf-8") as f:
            json.dump([l.json() for l in lobbies], f, indent=2)

        await interaction.response.send_message(
            files=[
                discord.File(users_file),
                discord.File(lobbies_file),
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
bot.run(DISCORD_BOT_TOKEN, log_handler=None)  # must be last
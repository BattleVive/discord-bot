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

#saving this shit in json to do move to it db or figure out better way to do this 
ROLES_FILE = "Roles.json"
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



def load_roles() -> dict:
    if not os.path.exists(ROLES_FILE):
        return {}
    with open(ROLES_FILE, "r") as f:
        return json.load(f)


def save_roles(data: dict) -> None:
    with open(ROLES_FILE, "w") as f:
        json.dump(data, f, indent=2)


async def create_roles(guild: discord.Guild):
    all_roles = load_roles()
    guild_roles = all_roles.get(str(guild.id), {})

    for rank in User.RANKS:
        role = await guild.create_role(name=rank[1])
        guild_roles[rank[1]] = role.id
    battlevive_role = await guild.create_role(name="Battlevive")
    guild_roles["Battlevive player"]= battlevive_role.id
    all_roles[str(guild.id)] = guild_roles
    save_roles(all_roles)

async def give_role(member: discord.Member, rank_name: str):
    all_roles = load_roles()
    guild_roles = all_roles.get(str(member.guild.id), {})
    role_id = guild_roles.get(rank_name)

    if role_id is None:
        raise ValueError(f"No stored role ID for rank '{rank_name}' in this guild")

    role = member.guild.get_role(role_id)
    if role is None:
        raise ValueError(f"Role ID {role_id} no longer exists in this guild")

    await member.add_roles(role)
async def give_battlevive_role():
    rank_name = "Battlevive player"
    all_roles = load_roles()

    for guild in bot.guilds:
        guild_roles = all_roles.get(str(guild.id), {})
        role_id = guild_roles.get(rank_name)

        if role_id is None:
            continue  # this guild has no stored role for this rank, skip

        role = guild.get_role(role_id)
        if role is None:
            continue  # role was deleted manually, skip

        for user in battlevive_users:
            member = guild.get_member(user.discord_id)

            if member is None:
                try:
                    member = await guild.fetch_member(user.discord_id)
                except discord.NotFound:
                    continue  # user not in this guild, skip
                except discord.HTTPException:
                    continue  # other API error, skip

            await member.add_roles(role)



async def give_rank_roles():
    all_roles = load_roles()

    for guild in bot.guilds:
        guild_roles = all_roles.get(str(guild.id), {})
        rank_role_ids = {guild_roles[rank[1]] for rank in User.RANKS if rank[1] in guild_roles}

        for user in battlevive_users:
            member = guild.get_member(user.discord_id)

            if member is None:
                try:
                    member = await guild.fetch_member(user.discord_id)
                except discord.NotFound:
                    continue
                except discord.HTTPException:
                    continue

            rank_name = user.rank()
            role_id = guild_roles.get(rank_name)

            if role_id is None:
                continue

            role = guild.get_role(role_id)
            if role is None:
                continue

            old_rank_roles = [
                r for r in member.roles
                if r.id in rank_role_ids and r.id != role_id
            ]

            if old_rank_roles:
                await member.remove_roles(*old_rank_roles)

            if role not in member.roles:
                await member.add_roles(role)




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


@tasks.loop(minutes=1)
async def refresh_loop():
    global battlevive_users, lobbies

    battlevive_users, lobbies = await refresh_all_data()

    await give_battlevive_role()
    await give_rank_roles()


intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!bt", intents=intents, strip_after_prefix=True)


@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    logger.info(f"Received message: '{message.content}'from {message.author}")
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




@bot.tree.command(name="create_roles", description="Create required roles")
async def create_roles_slash(interaction: discord.Interaction):
    await create_roles(interaction.guild)
    await interaction.response.send_message("Created roles")  


@bot.tree.command(
    name="debug_get_battlevive_data",
    description="Administrator-only, refresh and dump all data from battlevive unformatted",
)
@app_commands.checks.has_permissions(administrator=True)
async def debug_get_battlevive_data(interaction: discord.Interaction):
    global battlevive_users, lobbies

    try:
        logger.info("debug_get_battlevive_data called by %s", interaction.user)

        await interaction.response.defer(ephemeral=True)

        battlevive_users, lobbies = await refresh_all_data()

        users_file = os.path.join(DATA_DIR, "users.json")
        lobbies_file = os.path.join(DATA_DIR, "lobbies.json")

        with open(users_file, "w", encoding="utf-8") as f:
            json.dump([u.json() for u in battlevive_users], f, indent=2)

        with open(lobbies_file, "w", encoding="utf-8") as f:
            json.dump([l.json() for l in lobbies], f, indent=2)

        await interaction.followup.send(
            files=[
                discord.File(users_file),
                discord.File(lobbies_file),
            ],
            ephemeral=True,
        ) 

    except Exception:
        logger.exception("debug_get_battlevive_data failed")

        if interaction.response.is_done():
            await interaction.followup.send(
                "Command failed. Check bot.log.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "Command failed. Check bot.log.",
                ephemeral=True,
            )

@debug_get_battlevive_data.error
async def admin_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message(
            "You must be an administrator to use this command.",
            ephemeral=True
        )

bot.run(DISCORD_BOT_TOKEN, log_handler=None)  # must be last
#!/usr/bin/env python3
# pyrefly: ignore [missing-import]
from requests import api
# pyrefly: ignore [missing-import]
import discord
# pyrefly: ignore [missing-import]
from discord import app_commands
# pyrefly: ignore [missing-import]
from discord.ext import tasks,commands
# pyrefly: ignore [missing-import]
from dotenv import load_dotenv
import os,asyncio,requests
import json
from db import init_pool, close_pool, get_pool
import db
from battlevive_api import BattlevivieTokenManager,User,Lobby,SeasonRating,query_lobbies,query_users,query_season_ratings,sync_battlevive_data_to_db,sync_users_to_db,sync_lobbies_to_db,sync_season_ratings_to_db
from logs import discord_logger,logger
from io import BytesIO
# pyrefly: ignore [missing-import]
from PIL import Image
from images import build_card



DATABASE_URL = os.getenv("DATABASE_URL")  # postgresql://user:pass@db:5432/battlevive

#creating dirs if  not present
DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)


#getting env variables(all besides bootstrap keys, they are inline with token objects)
load_dotenv()
DISCORD_BOT_TOKEN = os.getenv("DISCORD_TOKEN")
battlevive_tokens= BattlevivieTokenManager(JWT_token=os.getenv("BOOTSTRAP_JWT"),refresh_token=os.getenv("BOOTSTRAP_REFRESH_TOKEN"))

battlevive_users: list[User] = []
lobbies: list[Lobby] = []
season_ratings: list[SeasonRating]=[]


async def refresh_all_data():
    # Fetches run concurrently - network I/O, order doesn't matter here.
    results = await asyncio.gather(
        query_users(battlevive_tokens.JWT_token),
        query_lobbies(battlevive_tokens.JWT_token),
        query_season_ratings(battlevive_tokens.JWT_token),
        return_exceptions=True,
    )
    for result in results:
        if isinstance(result, Exception):
            raise result

    users, lobbies, season_ratings = results

    # Sync runs sequentially, users first - lobbies.creator_id and
    # season_ratings.user_id both FK into users, so this order is what
    # actually prevents the ForeignKeyViolationError, regardless of which
    # fetch happened to finish first above.
    await sync_battlevive_data_to_db(users, lobbies,season_ratings)

    return users, lobbies, season_ratings


async def create_roles(guild: discord.Guild):
    logger.info("Creating Battlevive roles in guild '%s' (%s)", guild.name, guild.id)

    for _, rank_name in SeasonRating.RANKS:
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
    global battlevive_users 
    #refresh data and save it into db
    battlevive_users = await query_users(battlevive_tokens.JWT_token)
    await sync_users_to_db(users= battlevive_users)

    rank_name = "Battlevive Player"
    users  =  await db.get_users()

    for guild in bot.guilds:
        role = discord.utils.get(guild.roles, name=rank_name)
        if role is None:
            continue
        for user in users:
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
                if member is not None:
                    pool = get_pool()
                    status = await pool.execute("UPDATE users SET discord_id = $1 WHERE LOWER(discord_username) = $2", member.id, user.discord_username.lower())
                    logger.debug(f"Trying to set id ={member.id}  for user {user.discord_username}")
                    logger.debug(f"Update status: {status}")

            if member is None:
                logger.debug("No member found for %s in guild '%s'", user.discord_username, guild.name)
                continue

            #logger.debug(f"id: {member.id} username: {member.name}")

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

# dont use upstream changed broken !!!
async def give_rank_roles():

    global battlevive_users, season_ratings
    #refresh data and save it into db
    battlevive_users = await query_users(battlevive_tokens.JWT_token)
    season_ratings = await query_season_ratings(battlevive_tokens.JWT_token)
    await sync_users_to_db(users=battlevive_users)
    await sync_season_ratings_to_db(ratings=season_ratings)

    pool = get_pool()
    users = await pool.fetch("SELECT users.id, users.discord_id, users.discord_username, season_ratings.mmr FROM users INNER JOIN season_ratings ON season_ratings.user_id = users.id")

    for guild in bot.guilds:
        for user in users:
            member = None
            if user["discord_id"] is not None:
                member = guild.get_member(user["discord_id"])
                if member is None:
                    try:
                        member = await guild.fetch_member(user["discord_id"])
                    except (discord.NotFound, discord.HTTPException):
                        continue
            else:
                results = await guild.query_members(query=user["discord_username"])
                member = discord.utils.get(results, display_name=user["discord_username"])

            if member is None:
                logger.debug("No member found for %s in guild '%s'", user["discord_username"], guild.name)
                continue

            logger.debug(f"id: {member.id} username: {member.name}")

            rank_name = SeasonRating.rank(user["mmr"])
            role = discord.utils.get(guild.roles, name=rank_name)

            if role is None:
                logger.debug(
                    "Rank role '%s' not found in guild '%s' (%s), skipping user %s.",
                    rank_name, guild.name, guild.id, member,
                )
                continue
            old_rank_roles = [
                r for r in member.roles
                if r.name in [rank for _, rank in SeasonRating.RANKS]
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
    global battlevive_users, lobbies,season_ratings

    try:
        battlevive_users, lobbies,season_ratings = await refresh_all_data()
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
    await init_pool(DATABASE_URL) 
    revalidate_tokens.start()
    refresh_loop.start()
    #await bot.tree.sync()  # <-- registers slash commands with Discord
    bot.tree.copy_global_to(guild=discord.Object(id=1524804820098224240))
    await bot.tree.sync(guild=discord.Object(id=1524804820098224240)) # for prod change to global sync 


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
    name="debug_get_db_data",
    description="Dump all data from db unformatted",
)
async def debug_get_db_data(interaction: discord.Interaction):
    try:
        logger.info("debug_get_battlevive_data called by %s", interaction.user)

        
        users_file = os.path.join(DATA_DIR, "users.json")
        lobbies_file = os.path.join(DATA_DIR, "lobbies.json")
        ratings_file =os.path.join(DATA_DIR,"ratings.json")

        lobbies = await db.get_lobbies() 
        users = await db.get_users()
        ratings = await db.get_season_ratings()

        logger.debug("Dumping %d users and %d lobbies and ratings %d.", len(battlevive_users), len(lobbies),len(season_ratings))
        with open(users_file, "w", encoding="utf-8") as f:
            json.dump([u.json() for u in users], f, indent=2)

        with open(lobbies_file, "w", encoding="utf-8") as f:
            json.dump([l.json() for l in lobbies], f, indent=2)

        with open(ratings_file, "w", encoding="utf-8") as f:
            json.dump([r.json() for r in ratings], f, indent=2)

        await interaction.response.send_message(
            files=[
                discord.File(users_file),
                discord.File(lobbies_file),
                discord.File(ratings_file),
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
    name="debug_get_battlevive_data",
    description="Dump all data from battlevive unformatted",
)
async def debug_get_battlevive_data(interaction: discord.Interaction):
    try:
        logger.info("debug_get_battlevive_data called by %s", interaction.user)

        logger.debug("Dumping %d users and %d lobbies and ratings %d.", len(battlevive_users), len(lobbies),len(season_ratings))
        users_file = os.path.join(DATA_DIR, "users.json")
        lobbies_file = os.path.join(DATA_DIR, "lobbies.json")
        ratings_file =os.path.join(DATA_DIR,"ratings.json")
        with open(users_file, "w", encoding="utf-8") as f:
            json.dump([u.json() for u in battlevive_users], f, indent=2)

        with open(lobbies_file, "w", encoding="utf-8") as f:
            json.dump([l.json() for l in lobbies], f, indent=2)

        with open(ratings_file, "w", encoding="utf-8") as f:
            json.dump([r.json() for r in season_ratings], f, indent=2)

        await interaction.response.send_message(
            files=[
                discord.File(users_file),
                discord.File(lobbies_file),
                discord.File(ratings_file),
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
    name        = "rank",
    description = "Display your rank"
)
async def rank_command(interaction: discord.Interaction):
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
            interaction.user.id,  # BIGINT in DB – no str() cast
        )

        if user is None:
            logger.debug("rank: no DB record for discord_id=%s", interaction.user.id)
            await interaction.response.send_message(
                "You are not registered.", ephemeral=True
            )
            return

        mmr          = user["mmr"]
        rank_current = SeasonRating.rank(mmr)

        rank_next    = rank_current
        mmr_required = mmr
        for i, (threshold, name) in enumerate(SeasonRating.RANKS):
            if mmr >= threshold:
                if i > 0:
                    mmr_required, rank_next = SeasonRating.RANKS[i - 1]
                break

        logger.debug(
            "rank: user=%s mmr=%d current=%s next=%s mmr_required=%d",
            interaction.user.id, mmr, rank_current, rank_next, mmr_required,
        )

        avatar_bytes = await interaction.user.display_avatar.with_size(128).read()
        avatar       = Image.open(BytesIO(avatar_bytes))

        png = build_card(
            avatar       = avatar,
            display_name = interaction.user.display_name,
            rank_current = rank_current,
            rank_next    = rank_next,
            mmr_current  = mmr,
            mmr_required = mmr_required,
            wins         = user["wins"],
            losses       = user["losses"],
        )

        card_path = os.path.join(DATA_DIR, f"profile_{interaction.user.id}.png")
        with open(card_path, "wb") as f:
            f.write(png)

        file  = discord.File(card_path, filename="profile.png")
        embed = discord.Embed(title=interaction.user.display_name)
        embed.set_image(url="attachment://profile.png")
        await interaction.response.send_message(embed=embed, file=file)
        logger.info("rank: sent profile card to %s (%s)", interaction.user, interaction.user.id)

    except Exception:
        logger.exception("rank command failed for %s (%s)", interaction.user, interaction.user.id)
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "Command failed. Check bot.log.", ephemeral=True
            )


@bot.event
async def on_close():
    await close_pool()

bot.run(DISCORD_BOT_TOKEN, log_handler=None)  # must be last
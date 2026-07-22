from __future__ import annotations

import discord

from . import db
from .battlevive_api import BattleviveTokenManager
from .battlevive_api import query_season_ratings
from .battlevive_api import query_users
from .db import get_pool
from .db import sync_season_ratings_to_db
from .db import sync_users_to_db
from .logs import logger
from .models import SeasonRating
from .models import User


async def create_roles(guild: discord.Guild) -> None:
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


async def give_battlevive_role(
    bot: discord.Client,
    token_manager: BattleviveTokenManager,
) -> list[User]:
    battlevive_users = await query_users(token_manager.JWT_token)
    await sync_users_to_db(users=battlevive_users)

    role_name = "Battlevive Player"
    users = await db.get_users()

    for guild in bot.guilds:
        role = discord.utils.get(guild.roles, name=role_name)
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
                        continue
                    except discord.HTTPException:
                        continue
                    except discord.Forbidden:
                        continue
            else:
                results = await guild.query_members(query=user.discord_username)
                member = discord.utils.get(results, display_name=user.discord_username)
                if member is not None:
                    pool = get_pool()
                    status = await pool.execute(
                        "UPDATE users SET discord_id = $1 WHERE LOWER(discord_username) = $2",
                        member.id,
                        user.discord_username.lower(),
                    )
                    logger.debug(
                        "Trying to set id =%s  for user %s",
                        member.id,
                        user.discord_username,
                    )
                    logger.debug("Update status: %s", status)

            if member is None:
                logger.debug(
                    "No member found for %s in guild '%s'",
                    user.discord_username,
                    guild.name,
                )
                continue

            if role not in member.roles:
                try:
                    await member.add_roles(role)
                    logger.debug(
                        "Gave role '%s' to %s in guild '%s' (%s).",
                        role.name,
                        member,
                        guild.name,
                        guild.id,
                    )
                except (discord.Forbidden, discord.HTTPException):
                    logger.exception(
                        "Failed to give role '%s' to %s in guild '%s' (%s).",
                        role.name,
                        member,
                        guild.name,
                        guild.id,
                    )

    return battlevive_users


async def give_rank_roles(
    bot: discord.Client,
    token_manager: BattleviveTokenManager,
) -> tuple[list[User], list[SeasonRating]]:
    battlevive_users = await query_users(token_manager.JWT_token)
    season_ratings = await query_season_ratings(token_manager.JWT_token)
    await sync_users_to_db(users=battlevive_users)
    await sync_season_ratings_to_db(ratings=season_ratings)

    pool = get_pool()
    users = await pool.fetch(
        """
        SELECT
            users.id,
            users.discord_id,
            users.discord_username,
            season_ratings.mmr
        FROM users
        INNER JOIN season_ratings ON season_ratings.user_id = users.id
        """
    )

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
                logger.debug(
                    "No member found for %s in guild '%s'",
                    user["discord_username"],
                    guild.name,
                )
                continue

            logger.debug("id: %s username: %s", member.id, member.name)

            rank_name = SeasonRating.rank(user["mmr"])
            role = discord.utils.get(guild.roles, name=rank_name)

            if role is None:
                logger.debug(
                    "Rank role '%s' not found in guild '%s' (%s), skipping user %s.",
                    rank_name,
                    guild.name,
                    guild.id,
                    member,
                )
                continue

            old_rank_roles = [
                role_item
                for role_item in member.roles
                if role_item.name in [rank for _, rank in SeasonRating.RANKS]
            ]
            try:
                if old_rank_roles:
                    await member.remove_roles(*old_rank_roles)
                if role not in member.roles:
                    await member.add_roles(role)
                    logger.debug(
                        "Updated rank role for %s to '%s' in guild '%s' (%s).",
                        member,
                        rank_name,
                        guild.name,
                        guild.id,
                    )
            except (discord.Forbidden, discord.HTTPException):
                logger.exception(
                    "Failed to update rank role for %s in guild '%s' (%s).",
                    member,
                    guild.name,
                    guild.id,
                )
                continue

    return battlevive_users, season_ratings

from __future__ import annotations

import discord

from . import db
from .db import get_pool
from .logs import logger
from .models import SeasonRating


def _normalized_username(value: str | None) -> str:
    return value.strip().casefold() if value else ""


def _matching_member(
    members: list[discord.Member],
    discord_username: str,
) -> discord.Member | None:
    """Prefer a Discord account name, then an unambiguous display name."""
    expected = _normalized_username(discord_username)
    if not expected:
        return None

    account_matches = {
        member.id: member
        for member in members
        if _normalized_username(getattr(member, "name", None)) == expected
    }
    if len(account_matches) == 1:
        return next(iter(account_matches.values()))
    if len(account_matches) > 1:
        return None

    display_matches = {
        member.id: member
        for member in members
        if any(
            _normalized_username(getattr(member, attribute, None)) == expected
            for attribute in ("display_name", "global_name", "nick")
        )
    }
    if len(display_matches) == 1:
        return next(iter(display_matches.values()))
    return None


async def _resolve_member(
    guild: discord.Guild,
    discord_id: int | None,
    discord_username: str,
) -> discord.Member | None:
    if discord_id is not None:
        member = guild.get_member(discord_id)
        if member is not None:
            return member
        try:
            return await guild.fetch_member(discord_id)
        except (discord.NotFound, discord.HTTPException, discord.Forbidden):
            return None

    member = _matching_member(list(guild.members), discord_username)
    if member is not None:
        return member

    results: dict[int, discord.Member] = {}
    queries = [discord_username]
    folded_username = discord_username.casefold()
    if folded_username != discord_username:
        queries.append(folded_username)
    for query in queries:
        try:
            for result in await guild.query_members(query=query, limit=100):
                results[result.id] = result
        except (discord.HTTPException, discord.Forbidden):
            logger.exception(
                "Failed to query Discord members for %s in guild '%s' (%s).",
                discord_username,
                guild.name,
                guild.id,
            )
            return None
    return _matching_member(list(results.values()), discord_username)


async def _resolve_and_link_member(
    guild: discord.Guild,
    user_id: str,
    discord_id: int | None,
    discord_username: str,
) -> discord.Member | None:
    member = await _resolve_member(guild, discord_id, discord_username)
    if member is None or discord_id is not None:
        return member

    if not await db.set_user_discord_id(user_id, member.id):
        logger.warning(
            "Could not link Battlevive user %s (%s) to Discord ID %s; "
            "the ID may already be linked to another user.",
            discord_username,
            user_id,
            member.id,
        )
        return None
    logger.info(
        "Linked Battlevive user %s (%s) to Discord member %s (%s).",
        discord_username,
        user_id,
        member,
        member.id,
    )
    return member


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
) -> None:
    role_name = "Battlevive Player"
    users = await db.get_users()

    for guild in bot.guilds:
        role = discord.utils.get(guild.roles, name=role_name)
        if role is None:
            continue

        for user in users:
            member = await _resolve_and_link_member(
                guild,
                user.id,
                user.discord_id,
                user.discord_username,
            )

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



async def give_rank_roles(
    bot: discord.Client,
) -> None:
    pool = get_pool()
    users = await pool.fetch(
        """
        WITH current_season AS (
            SELECT season_year, season_number
            FROM season_ratings
            ORDER BY updated_at DESC
            LIMIT 1
        )
        SELECT
            users.id,
            users.discord_id,
            users.discord_username,
            season_ratings.mmr
        FROM users
        INNER JOIN season_ratings ON season_ratings.user_id = users.id
        INNER JOIN current_season
            ON current_season.season_year = season_ratings.season_year
           AND current_season.season_number = season_ratings.season_number
        """
    )

    for guild in bot.guilds:
        for user in users:
            member = await _resolve_and_link_member(
                guild,
                str(user["id"]),
                user["discord_id"],
                user["discord_username"],
            )

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
                and role_item != role
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

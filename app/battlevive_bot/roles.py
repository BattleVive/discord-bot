from __future__ import annotations

from dataclasses import dataclass, field

import discord

from . import db
from .db import get_pool
from .logs import logger
from .models import SeasonRating


BATTLEVIVE_PLAYER_ROLE = "Battlevive Player"
ACTIVE_LOBBY_ROLE = "Active Lobby"
RANK_ROLE_NAMES_ORDERED = tuple(name for _, name in SeasonRating.RANKS)
RANK_ROLE_NAMES = frozenset(RANK_ROLE_NAMES_ORDERED)
REQUIRED_ROLE_NAMES = (
    *RANK_ROLE_NAMES_ORDERED,
    BATTLEVIVE_PLAYER_ROLE,
    ACTIVE_LOBBY_ROLE,
)


@dataclass(slots=True)
class RoleCreationResult:
    created: list[str] = field(default_factory=list)
    existing: list[str] = field(default_factory=list)
    rejected: dict[str, str] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)


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


def _existing_role_problem(
    role: discord.Role,
    bot_member: discord.Member,
) -> str | None:
    if getattr(role, "managed", False):
        return "the existing role is managed by an integration"
    permissions = getattr(role, "permissions", None)
    if permissions is not None and getattr(permissions, "value", 0):
        return "the existing role has privileged permissions"
    top_role = getattr(bot_member, "top_role", None)
    role_position = getattr(role, "position", None)
    top_position = getattr(top_role, "position", None)
    if (
        role_position is not None
        and top_position is not None
        and role_position >= top_position
    ):
        return "the existing role is not below the bot's highest role"
    is_assignable = getattr(role, "is_assignable", None)
    if callable(is_assignable) and not is_assignable():
        return "the existing role cannot be assigned by the bot"
    return None


def _safe_assignable_role(
    guild: discord.Guild,
    role_name: str,
) -> discord.Role | None:
    role = discord.utils.get(guild.roles, name=role_name)
    bot_member = guild.me
    if role is None or bot_member is None:
        return None
    problem = _existing_role_problem(role, bot_member)
    if problem is not None:
        logger.warning(
            "Refusing unsafe role '%s' in guild '%s' (%s): %s.",
            role_name,
            guild.name,
            guild.id,
            problem,
        )
        return None
    return role


async def create_roles(guild: discord.Guild) -> RoleCreationResult:
    logger.info("Creating Battlevive roles in guild '%s' (%s)", guild.name, guild.id)
    result = RoleCreationResult()
    bot_member = guild.me
    if (
        bot_member is None
        or not bot_member.guild_permissions.manage_roles
        or bot_member.top_role <= guild.default_role
    ):
        for role_name in REQUIRED_ROLE_NAMES:
            result.failed[role_name] = "the bot lacks Manage Roles"
        return result

    for role_name in REQUIRED_ROLE_NAMES:
        existing_role = discord.utils.get(guild.roles, name=role_name)

        if existing_role is not None:
            problem = _existing_role_problem(existing_role, bot_member)
            if problem is not None:
                result.rejected[role_name] = problem
                logger.warning(
                    "Rejected unsafe existing role '%s' in guild '%s' (%s): %s.",
                    role_name,
                    guild.name,
                    guild.id,
                    problem,
                )
                continue
            if role_name == ACTIVE_LOBBY_ROLE and getattr(
                existing_role,
                "mentionable",
                False,
            ):
                try:
                    await existing_role.edit(
                        mentionable=False,
                        reason="Battlevive role safety setup",
                    )
                except discord.Forbidden:
                    result.failed[role_name] = (
                        "Discord denied disabling role mentions"
                    )
                    logger.exception(
                        "Missing permissions to make role '%s' non-mentionable "
                        "in guild '%s' (%s).",
                        role_name,
                        guild.name,
                        guild.id,
                    )
                    continue
                except discord.HTTPException:
                    result.failed[role_name] = (
                        "Discord rejected disabling role mentions"
                    )
                    logger.exception(
                        "Failed to make role '%s' non-mentionable in guild '%s' (%s).",
                        role_name,
                        guild.name,
                        guild.id,
                    )
                    continue
            logger.debug(
                "Role '%s' already exists in guild '%s' (%s), skipping.",
                role_name,
                guild.name,
                guild.id,
            )
            result.existing.append(role_name)
            continue

        try:
            role = await guild.create_role(
                name=role_name,
                permissions=discord.Permissions.none(),
                mentionable=False,
                reason="Battlevive role setup",
            )
            result.created.append(role_name)
            logger.info(
                "Created role '%s' (%s) in guild '%s' (%s).",
                role.name,
                role.id,
                guild.name,
                guild.id,
            )
        except discord.Forbidden:
            result.failed[role_name] = "Discord denied role creation"
            logger.exception(
                "Missing permissions to create role '%s' in guild '%s' (%s).",
                role_name,
                guild.name,
                guild.id,
            )
        except discord.HTTPException:
            result.failed[role_name] = "Discord rejected role creation"
            logger.exception(
                "Failed to create role '%s' in guild '%s' (%s).",
                role_name,
                guild.name,
                guild.id,
            )

    return result


async def give_battlevive_role(
    bot: discord.Client,
    guild: discord.Guild | None = None,
) -> None:
    users = await db.get_users()

    guilds = [guild] if guild is not None else bot.guilds
    for target_guild in guilds:
        role = _safe_assignable_role(
            target_guild,
            BATTLEVIVE_PLAYER_ROLE,
        )
        if role is None:
            continue

        for user in users:
            member = await _resolve_and_link_member(
                target_guild,
                user.id,
                user.discord_id,
                user.discord_username,
            )

            if member is None:
                logger.debug(
                    "No member found for %s in guild '%s'",
                    user.discord_username,
                    target_guild.name,
                )
                continue

            if role not in member.roles:
                try:
                    await member.add_roles(role)
                    logger.debug(
                        "Gave role '%s' to %s in guild '%s' (%s).",
                        role.name,
                        member,
                        target_guild.name,
                        target_guild.id,
                    )
                except (discord.Forbidden, discord.HTTPException):
                    logger.exception(
                        "Failed to give role '%s' to %s in guild '%s' (%s).",
                        role.name,
                        member,
                        target_guild.name,
                        target_guild.id,
                    )


async def give_rank_roles(
    bot: discord.Client,
    guild: discord.Guild | None = None,
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

    guilds = [guild] if guild is not None else bot.guilds
    for target_guild in guilds:
        for user in users:
            member = await _resolve_and_link_member(
                target_guild,
                str(user["id"]),
                user["discord_id"],
                user["discord_username"],
            )

            if member is None:
                logger.debug(
                    "No member found for %s in guild '%s'",
                    user["discord_username"],
                    target_guild.name,
                )
                continue

            logger.debug("id: %s username: %s", member.id, member.name)

            rank_name = SeasonRating.rank(user["mmr"])
            role = _safe_assignable_role(target_guild, rank_name)

            if role is None:
                logger.debug(
                    "Rank role '%s' not found in guild '%s' (%s), skipping user %s.",
                    rank_name,
                    target_guild.name,
                    target_guild.id,
                    member,
                )
                continue

            old_rank_roles = [
                role_item
                for role_item in member.roles
                if role_item.name in RANK_ROLE_NAMES
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
                        target_guild.name,
                        target_guild.id,
                    )
            except (discord.Forbidden, discord.HTTPException):
                logger.exception(
                    "Failed to update rank role for %s in guild '%s' (%s).",
                    member,
                    target_guild.name,
                    target_guild.id,
                )
                continue


async def reconcile_member_roles(member: discord.Member) -> None:
    """Reconcile one joining member from local data without upstream requests."""
    pool = get_pool()
    user = await pool.fetchrow(
        """
        WITH current_season AS (
            SELECT season_year, season_number
            FROM season_ratings
            ORDER BY updated_at DESC
            LIMIT 1
        )
        SELECT users.id, users.discord_id, users.discord_username,
               season_ratings.mmr
        FROM users
        LEFT JOIN season_ratings
          ON season_ratings.user_id = users.id
         AND (season_ratings.season_year, season_ratings.season_number) = (
             SELECT season_year, season_number FROM current_season
         )
        WHERE users.discord_id = $1
        """,
        member.id,
    )
    if user is None:
        candidate_names = {
            value.strip().lower()
            for value in (
                getattr(member, "name", None),
                getattr(member, "display_name", None),
                getattr(member, "global_name", None),
                getattr(member, "nick", None),
            )
            if value and value.strip()
        }
        if not candidate_names:
            return
        rows = await pool.fetch(
            """
            WITH current_season AS (
                SELECT season_year, season_number
                FROM season_ratings
                ORDER BY updated_at DESC
                LIMIT 1
            )
            SELECT users.id, users.discord_id, users.discord_username,
                   season_ratings.mmr
            FROM users
            LEFT JOIN season_ratings
              ON season_ratings.user_id = users.id
             AND (season_ratings.season_year, season_ratings.season_number) = (
                 SELECT season_year, season_number FROM current_season
             )
            WHERE users.discord_id IS NULL
              AND lower(btrim(users.discord_username)) = ANY($1::text[])
            """,
            sorted(candidate_names),
        )
        user = next(
            (
                row
                for row in rows
                if _matching_member([member], row["discord_username"]) is member
            ),
            None,
        )
        if user is not None and not await db.set_user_discord_id(
            str(user["id"]),
            member.id,
        ):
            logger.warning(
                "Could not safely link joining member %s (%s).",
                member,
                member.id,
            )
            return
    if user is None:
        return

    player_role = _safe_assignable_role(
        member.guild,
        BATTLEVIVE_PLAYER_ROLE,
    )
    if player_role is not None and player_role not in member.roles:
        await member.add_roles(
            player_role,
            reason="Battlevive member join reconciliation",
        )

    mmr = user["mmr"]
    if mmr is None:
        return
    rank_role = _safe_assignable_role(
        member.guild,
        SeasonRating.rank(mmr),
    )
    if rank_role is None:
        return
    old_rank_roles = [
        role
        for role in member.roles
        if role.name in RANK_ROLE_NAMES and role != rank_role
    ]
    if old_rank_roles:
        await member.remove_roles(
            *old_rank_roles,
            reason="Battlevive member join reconciliation",
        )
    if rank_role not in member.roles:
        await member.add_roles(
            rank_role,
            reason="Battlevive member join reconciliation",
        )

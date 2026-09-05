from __future__ import annotations

from dataclasses import dataclass, field

import discord

from . import db
from .identity import IdentityStatus
from .identity import matching_member as _matching_member
from .identity import normalize_name as _normalized_username
from .identity import resolve_member_identity
from .identity import resolve_user_in_guild
from .logs import logger
from .models import SeasonRating


ACTIVE_LOBBY_ROLE = "Active Lobby"
WEBSITE_MODERATOR_ROLE = "Website Moderator"
GUIDE_UPDATES_ROLE = "Guide Updates"
NOTIFICATION_ROLE_NAMES = frozenset(
    {ACTIVE_LOBBY_ROLE, WEBSITE_MODERATOR_ROLE, GUIDE_UPDATES_ROLE}
)
RANK_ROLE_NAMES_ORDERED = tuple(name for _, name in SeasonRating.RANKS)
RANK_ROLE_NAMES = frozenset(RANK_ROLE_NAMES_ORDERED)
REQUIRED_ROLE_NAMES = (
    GUIDE_UPDATES_ROLE,
    *RANK_ROLE_NAMES_ORDERED,
)


@dataclass(slots=True)
class RoleCreationResult:
    created: list[str] = field(default_factory=list)
    existing: list[str] = field(default_factory=list)
    rejected: dict[str, str] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)
    safe_roles: dict[str, discord.Role] = field(default_factory=dict)


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


async def create_roles(
    guild: discord.Guild,
    *,
    skip_role_names: frozenset[str] = frozenset(),
) -> RoleCreationResult:
    logger.info("Creating Battlevive roles in guild '%s' (%s)", guild.name, guild.id)
    result = RoleCreationResult()
    bot_member = guild.me
    if (
        bot_member is None
        or not bot_member.guild_permissions.manage_roles
        or bot_member.top_role <= guild.default_role
    ):
        for role_name in REQUIRED_ROLE_NAMES:
            if role_name in skip_role_names:
                continue
            result.failed[role_name] = "the bot lacks Manage Roles"
        return result

    for role_name in REQUIRED_ROLE_NAMES:
        if role_name in skip_role_names:
            continue
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
            authoritative_role = existing_role
            if role_name in NOTIFICATION_ROLE_NAMES and getattr(
                existing_role,
                "mentionable",
                False,
            ):
                try:
                    authoritative_role = await existing_role.edit(
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
                if getattr(authoritative_role, "mentionable", False):
                    result.failed[role_name] = (
                        "Discord did not disable role mentions"
                    )
                    logger.warning(
                        "Role '%s' remained mentionable in guild '%s' (%s).",
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
            result.safe_roles[role_name] = authoritative_role
            continue

        try:
            role = await guild.create_role(
                name=role_name,
                permissions=discord.Permissions.none(),
                mentionable=False,
                reason="Battlevive role setup",
            )
            result.created.append(role_name)
            result.safe_roles[role_name] = role
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


async def give_rank_roles(
    bot: discord.Client,
    guild: discord.Guild | None = None,
) -> None:
    users = await db.get_pool().fetch(
        """
        WITH current_season AS (
            SELECT season_year, season_number
            FROM season_ratings
            ORDER BY updated_at DESC
            LIMIT 1
        )
        SELECT
            users.id,
            COALESCE(links.discord_id, users.discord_id) AS discord_id,
            users.discord_username,
            season_ratings.mmr
        FROM users
        LEFT JOIN season_ratings ON season_ratings.user_id = users.id
         AND (season_ratings.season_year, season_ratings.season_number) = (
             SELECT season_year, season_number FROM current_season
         )
        LEFT JOIN user_discord_links AS links ON links.user_id = users.id
        """
    )

    guilds = [guild] if guild is not None else bot.guilds
    for target_guild in guilds:
        matched_member_ids: set[int] = set()
        resolution_complete = True
        for user in users:
            member, complete = await resolve_user_in_guild(target_guild, dict(user))
            resolution_complete = resolution_complete and complete

            if member is None:
                logger.debug(
                    "No member found for %s in guild '%s'",
                    user["discord_username"],
                    target_guild.name,
                )
                continue
            matched_member_ids.add(member.id)

            if user["mmr"] is None:
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
                if role_item.name in RANK_ROLE_NAMES and role_item != role
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

        if not resolution_complete:
            logger.warning(
                "Skipping stale MMR-role removal in guild '%s' (%s) because member resolution was incomplete.",
                target_guild.name,
                target_guild.id,
            )
            continue
        for member in target_guild.members:
            if member.id in matched_member_ids:
                continue
            stale_roles = [role for role in member.roles if role.name in RANK_ROLE_NAMES]
            if not stale_roles:
                continue
            try:
                await member.remove_roles(*stale_roles, reason="Battlevive membership reconciliation")
            except (discord.Forbidden, discord.HTTPException):
                logger.exception("Failed to remove stale MMR roles from member %s.", member.id)

async def reconcile_member_roles(member: discord.Member, refresh_on_miss=None) -> None:
    """Resolve an active member and apply its current MMR tier."""
    identity = await resolve_member_identity(member, refresh_on_miss)
    if identity.status is not IdentityStatus.LINKED or identity.user is None:
        return
    profile = await db.get_current_rank_profile(identity.user["id"])
    mmr = profile["mmr"] if profile is not None else None
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

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from enum import Enum
from typing import Any

import discord

from . import db
from .logs import logger


class IdentityStatus(Enum):
    LINKED = "linked"
    ABSENT = "absent"
    AMBIGUOUS = "ambiguous"
    REFRESH_FAILED = "refresh_failed"
    DATABASE_FAILED = "database_failed"


@dataclass(slots=True)
class IdentityResult:
    status: IdentityStatus
    user: dict[str, Any] | None = None
    member: discord.Member | None = None


def normalize_name(value: str | None) -> str:
    return value.strip().casefold() if value else ""


def member_names(member: discord.Member) -> set[str]:
    return {
        normalized
        for normalized in (
            normalize_name(getattr(member, "name", None)),
            normalize_name(getattr(member, "display_name", None)),
            normalize_name(getattr(member, "global_name", None)),
            normalize_name(getattr(member, "nick", None)),
        )
        if normalized
    }


def matching_member(
    members: Iterable[discord.Member],
    discord_username: str,
) -> discord.Member | None:
    expected = normalize_name(discord_username)
    if not expected:
        return None
    members = list(members)
    account_matches = {
        member.id: member
        for member in members
        if normalize_name(getattr(member, "name", None)) == expected
    }
    if len(account_matches) == 1:
        return next(iter(account_matches.values()))
    if len(account_matches) > 1:
        return None
    display_matches = {
        member.id: member
        for member in members
        if expected in member_names(member)
    }
    return next(iter(display_matches.values())) if len(display_matches) == 1 else None


async def resolve_member_identity(
    member: discord.Member,
    refresh_on_miss: Callable[[], Awaitable[bool]] | None = None,
) -> IdentityResult:
    """Resolve only active users, optionally refreshing once after a local miss."""
    refreshed = False
    while True:
        try:
            linked = await db.get_active_user_by_discord_id(member.id)
            if linked is not None:
                return IdentityResult(IdentityStatus.LINKED, linked, member)
            all_candidates = await db.find_active_users_by_names(member_names(member))
            if len(all_candidates) > 1:
                return IdentityResult(IdentityStatus.AMBIGUOUS)
            candidates = [
                candidate
                for candidate in all_candidates
                if candidate.get("linked_discord_id") is None
            ]
            exact = [
                candidate
                for candidate in candidates
                if normalize_name(candidate["discord_username"]) in member_names(member)
            ]
            if len(exact) > 1:
                return IdentityResult(IdentityStatus.AMBIGUOUS)
            if len(exact) == 1:
                candidate = exact[0]
                if await db.set_user_discord_id(candidate["id"], member.id):
                    candidate["discord_id"] = member.id
                    return IdentityResult(IdentityStatus.LINKED, candidate, member)
                linked = await db.get_active_user_by_discord_id(member.id)
                if linked is not None:
                    return IdentityResult(IdentityStatus.LINKED, linked, member)
                return IdentityResult(IdentityStatus.AMBIGUOUS)
        except Exception:
            logger.exception("Database identity lookup failed for Discord member %s.", member.id)
            return IdentityResult(IdentityStatus.DATABASE_FAILED)

        if refreshed or refresh_on_miss is None:
            return IdentityResult(IdentityStatus.ABSENT)
        try:
            if not await refresh_on_miss():
                return IdentityResult(IdentityStatus.REFRESH_FAILED)
        except Exception:
            logger.exception("Identity refresh failed for Discord member %s.", member.id)
            return IdentityResult(IdentityStatus.REFRESH_FAILED)
        refreshed = True


async def resolve_user_in_guild(
    guild: discord.Guild,
    user: dict[str, Any],
) -> tuple[discord.Member | None, bool]:
    """Return a member and whether Discord resolution was complete."""
    discord_id = user.get("discord_id")
    if discord_id is not None:
        member = guild.get_member(discord_id)
        if member is not None:
            return member, True
        try:
            return await guild.fetch_member(discord_id), True
        except discord.NotFound:
            return None, True
        except (discord.Forbidden, discord.HTTPException):
            return None, False

    member = matching_member(guild.members, user["discord_username"])
    if member is None:
        try:
            queried = await guild.query_members(query=user["discord_username"], limit=100)
        except (discord.Forbidden, discord.HTTPException):
            return None, False
        member = matching_member(queried, user["discord_username"])
    if member is None:
        return None, True
    if not await db.set_user_discord_id(user["id"], member.id):
        return None, True
    return member, True

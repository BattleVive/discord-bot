"""Safe rank-role application for members selected by exact Discord IDs."""
from __future__ import annotations

from typing import Any

import discord

from .rank import RANKS


RANK_ROLE_NAMES = frozenset(name for _, name in RANKS)
GUIDE_UPDATES_ROLE = "Guide Updates"
REQUIRED_ROLE_NAMES = (GUIDE_UPDATES_ROLE, *(name for _, name in RANKS))


def _rank_name(mmr: int) -> str:
    """Normalize a Battlevive rank name."""
    return next(name for threshold, name in RANKS if mmr >= threshold)


def _safe_role(guild: Any, name: str) -> Any | None:
    """Return whether an existing Discord role is safe for the bot to manage."""
    role = next((item for item in getattr(guild, "roles", ()) if getattr(item, "name", None) == name), None)
    bot_member = getattr(guild, "me", None)
    if role is None or bot_member is None or getattr(role, "managed", False):
        return None
    if getattr(getattr(role, "permissions", None), "value", 0):
        return None
    top = getattr(getattr(bot_member, "top_role", None), "position", None)
    position = getattr(role, "position", None)
    if isinstance(top, int) and isinstance(position, int) and position >= top:
        return None
    assignable = getattr(role, "is_assignable", None)
    return role if not callable(assignable) or assignable() else None


async def reconcile_member_rank(member: Any, player: dict[str, Any]) -> bool:
    """Apply one API-selected member's fresh profile tier without global stale-role removal."""
    mmr = player.get("mmr")
    if isinstance(mmr, bool) or not isinstance(mmr, int) or mmr < 0:
        return False
    target = _safe_role(member.guild, _rank_name(mmr))
    if target is None:
        return False
    old = [role for role in getattr(member, "roles", ())
           if getattr(role, "name", None) in RANK_ROLE_NAMES and role != target]
    if old:
        await member.remove_roles(*old, reason="BattleVive rank reconciliation")
    if target not in getattr(member, "roles", ()):
        await member.add_roles(target, reason="BattleVive rank reconciliation")
    return bool(old or target not in getattr(member, "roles", ()))


async def create_required_roles(guild: Any) -> tuple[list[Any], list[str], list[str]]:
    """Create the v1-compatible safe roles needed by supported gateway features."""
    bot_member = getattr(guild, "me", None)
    if bot_member is None or not getattr(getattr(bot_member, "guild_permissions", None), "manage_roles", False):
        raise RuntimeError("the bot requires Manage Roles")
    created: list[Any] = []
    existing: list[str] = []
    blocked: list[str] = []
    for name in REQUIRED_ROLE_NAMES:
        role = _safe_role(guild, name)
        if role is not None:
            existing.append(name)
            continue
        collision = next((item for item in getattr(guild, "roles", ()) if getattr(item, "name", None) == name), None)
        if collision is not None:
            blocked.append(name)
            continue
        role = await guild.create_role(
            name=name, permissions=discord.Permissions.none(), mentionable=False,
            reason="BattleVive role setup",
        )
        created.append(role)
    return created, existing, blocked

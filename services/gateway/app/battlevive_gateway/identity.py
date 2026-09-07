"""Safe, local identity binding from API player records to guild members."""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def normalize_name(value: str | None) -> str:
    return value.strip().casefold() if value else ""


def member_names(member: Any) -> set[str]:
    return {
        name
        for name in (
            normalize_name(getattr(member, "name", None)),
            normalize_name(getattr(member, "display_name", None)),
            normalize_name(getattr(member, "global_name", None)),
            normalize_name(getattr(member, "nick", None)),
        )
        if name
    }


def matching_member(members: Iterable[Any], player_name: str) -> Any | None:
    """Use the v1 exact-account-name-first ambiguity-safe matching algorithm."""
    expected = normalize_name(player_name)
    if not expected:
        return None
    candidates = list(members)
    account_matches = {
        member.id: member for member in candidates
        if normalize_name(getattr(member, "name", None)) == expected
    }
    if len(account_matches) == 1:
        return next(iter(account_matches.values()))
    if len(account_matches) > 1:
        return None
    display_matches = {
        member.id: member for member in candidates
        if expected in member_names(member)
    }
    return next(iter(display_matches.values())) if len(display_matches) == 1 else None


async def save_guild_identity_links(guild: Any, players: Iterable[dict[str, Any]], identities: Any) -> int:
    """Persist only unambiguous links for members already visible in this guild.

    The upstream API has no player directory, so this deliberately operates only
    on player records returned by a supported feature route. It never discovers
    or enumerates Discord identities remotely.
    """
    saved = 0
    for player in players:
        member_number = player.get("member_number", player.get("memberNumber"))
        player_name = player.get("player", player.get("name"))
        if isinstance(member_number, bool) or not isinstance(member_number, int) or member_number <= 0:
            continue
        if not isinstance(player_name, str):
            continue
        member = matching_member(getattr(guild, "members", ()), player_name)
        if member is None:
            continue
        if await identities.bind(member_number, int(member.id), "guild-member-name"):
            saved += 1
    return saved

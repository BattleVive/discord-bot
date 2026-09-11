"""Rank card model copied from the original rank threshold behavior."""
from __future__ import annotations

from typing import Any


RANKS = ((8000, "BATTLEVIVE"), (5500, "Diamond"), (3500, "Platinum"),
         (2000, "Gold"), (1000, "Silver"), (0, "Bronze"))


def rank_render_model(player: dict[str, Any]) -> dict[str, object]:
    """Build the renderer model for a player's rank card."""
    name, rank = player.get("name"), player.get("rank")
    mmr, wins, losses = player.get("mmr"), player.get("wins"), player.get("losses")
    if not isinstance(name, str) or not isinstance(rank, str):
        raise ValueError("player profile is invalid")
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in (mmr, wins, losses)):
        raise ValueError("player profile is invalid")
    current, required = rank, mmr
    for index, (threshold, label) in enumerate(RANKS):
        if mmr >= threshold:
            current = label
            if index:
                required, next_rank = RANKS[index - 1]
            else:
                next_rank = current
            break
    return {"username": name, "rank_current": current, "rank_next": next_rank,
            "mmr_current": mmr, "mmr_required": required, "wins": wins, "losses": losses}

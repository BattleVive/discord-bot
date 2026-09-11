"""Redacted, administrator-only diagnostic data for private integrations."""
from __future__ import annotations

import json
from typing import Any

from .integrations import IntegrationUnavailable


FEATURE_PATHS = (
    "/queue",
    "/stats",
    "/guides",
    "/leaderboard",
    "/active-matches",
    "/recent-matches",
)
_MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024
_MAX_GUIDE_EXPANSIONS = 20
_MAX_PLAYER_EXPANSIONS = 20


async def upstream_snapshot(upstream: Any) -> dict[str, object]:
    """Return only validated internal results and credential-safe failures."""
    routes: dict[str, object] = {}
    truncated: dict[str, int] = {}

    async def collect(path: str) -> dict[str, Any] | None:
        """Collect one credential-safe upstream diagnostic result."""
        try:
            result = await upstream.get_result(path, require_fresh=True)
        except IntegrationUnavailable:
            routes[path] = {
                "status": "unavailable",
                "error": "Upstream data is temporarily unavailable.",
            }
            return None
        except Exception:
            routes[path] = {"status": "failed", "error": "Unexpected diagnostic failure."}
            return None
        else:
            routes[path] = {
                "status": "ok",
                "freshness": result.freshness,
                "age_seconds": result.age_seconds,
                "data": result.data,
            }
            return result.data

    for path in FEATURE_PATHS:
        data = await collect(path)
        if data is None:
            continue
        if path == "/guides":
            guides = data.get("guides")
            if not isinstance(guides, list):
                continue
            guide_numbers = [guide.get("number") for guide in guides if isinstance(guide, dict)]
            valid_numbers = [number for number in guide_numbers if isinstance(number, int) and number > 0]
            if len(valid_numbers) > _MAX_GUIDE_EXPANSIONS:
                truncated["guides"] = len(valid_numbers) - _MAX_GUIDE_EXPANSIONS
            for number in valid_numbers[:_MAX_GUIDE_EXPANSIONS]:
                await collect(f"/guides/{number}")
                await collect(f"/guides/{number}/markdown")
        elif path == "/leaderboard":
            leaderboard = data.get("leaderboard")
            if not isinstance(leaderboard, list):
                continue
            member_numbers = [entry.get("member_number") for entry in leaderboard if isinstance(entry, dict)]
            valid_numbers = [number for number in member_numbers if isinstance(number, int) and number > 0]
            if len(valid_numbers) > _MAX_PLAYER_EXPANSIONS:
                truncated["players"] = len(valid_numbers) - _MAX_PLAYER_EXPANSIONS
            for member_number in valid_numbers[:_MAX_PLAYER_EXPANSIONS]:
                await collect(f"/players/{member_number}")
    snapshot: dict[str, object] = {"routes": routes}
    if truncated:
        snapshot["truncated"] = truncated
    return snapshot


def snapshot_attachment(snapshot: dict[str, object]) -> bytes:
    """Serialize diagnostics for Discord without producing oversized output."""
    encoded = json.dumps(snapshot, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8")
    if len(encoded) > _MAX_ATTACHMENT_BYTES:
        raise ValueError("Validated upstream diagnostics exceed Discord's attachment limit.")
    return encoded

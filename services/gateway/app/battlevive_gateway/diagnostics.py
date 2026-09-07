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


async def upstream_snapshot(upstream: Any) -> dict[str, object]:
    """Return only validated internal results and credential-safe failures."""
    routes: dict[str, object] = {}

    async def collect(path: str) -> dict[str, Any] | None:
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
            for guide in guides:
                number = guide.get("number") if isinstance(guide, dict) else None
                if isinstance(number, int) and number > 0:
                    await collect(f"/guides/{number}")
                    await collect(f"/guides/{number}/markdown")
        elif path == "/leaderboard":
            leaderboard = data.get("leaderboard")
            if not isinstance(leaderboard, list):
                continue
            for entry in leaderboard:
                member_number = entry.get("member_number") if isinstance(entry, dict) else None
                if isinstance(member_number, int) and member_number > 0:
                    await collect(f"/players/{member_number}")
    return {"routes": routes}


def snapshot_attachment(snapshot: dict[str, object]) -> bytes:
    """Serialize diagnostics for Discord without producing oversized output."""
    encoded = json.dumps(snapshot, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8")
    if len(encoded) > _MAX_ATTACHMENT_BYTES:
        raise ValueError("Validated upstream diagnostics exceed Discord's attachment limit.")
    return encoded

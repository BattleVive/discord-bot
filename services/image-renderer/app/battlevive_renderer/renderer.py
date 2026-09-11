"""Validate renderer models and convert them into PNG image payloads."""

from __future__ import annotations

import base64
import binascii
from io import BytesIO
from collections.abc import Mapping
from typing import Any

from PIL import Image

from .images import LeaderboardEntry
from .images import build_card
from .images import build_leaderboard_png

MAX_ROWS = 100
MAX_IMAGE_WIDTH = 4_096
MAX_IMAGE_HEIGHT = 16_384
WIDTH = 1280
HEADER_HEIGHT = 184
ROW_HEIGHT = 124


def render_leaderboard(model: dict[str, Any]) -> bytes:
    """Validate a leaderboard model and render it as PNG bytes."""
    if not isinstance(model, Mapping):
        raise ValueError("render model must be an object")
    entries = model.get("entries")
    if not isinstance(entries, list):
        raise ValueError("entries must be a list")
    if len(entries) > MAX_ROWS:
        raise ValueError("leaderboard supports at most 100 rows")
    if HEADER_HEIGHT + len(entries) * ROW_HEIGHT > MAX_IMAGE_HEIGHT or WIDTH > MAX_IMAGE_WIDTH:
        raise ValueError("leaderboard dimensions exceed renderer limits")
    validated: list[LeaderboardEntry] = []
    for row in entries:
        if not isinstance(row, Mapping):
            raise ValueError("each leaderboard row must be an object")
        try:
            validated.append(LeaderboardEntry(
                place=_positive_int(row["place"]), username=_text(row["username"], "username"), rank=_text(row["rank"], "rank"),
                mmr=_nonnegative_int(row["mmr"]), wins=_nonnegative_int(row["wins"]), losses=_nonnegative_int(row["losses"]),
                win_rate=float(row["win_rate"]),
            ))
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("invalid leaderboard row") from error
    return build_leaderboard_png(validated, _text(model.get("season", "Current Season"), "season"))


def render_rank(model: dict[str, Any]) -> bytes:
    """Validate a rank model and render it as PNG bytes."""
    if not isinstance(model, Mapping):
        raise ValueError("render model must be an object")
    avatar = _rank_avatar(model)
    try:
        return build_card(avatar, _text(model["username"], "username"), _text(model["rank_current"], "rank_current"),
                          _text(model["rank_next"], "rank_next"), _nonnegative_int(model["mmr_current"]),
                          _nonnegative_int(model["mmr_required"]), _nonnegative_int(model["wins"]), _nonnegative_int(model["losses"]))
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("invalid rank render model") from error


def _rank_avatar(model: Mapping[str, Any]) -> Image.Image:
    """Decode and validate a rank-card avatar image."""
    encoded = model.get("avatar_png_base64")
    if encoded is None:
        return Image.new("RGB", (128, 128), "#172638")
    if not isinstance(encoded, str) or len(encoded) > 512_000:
        raise ValueError("avatar payload is invalid")
    try:
        raw = base64.b64decode(encoded, validate=True)
        with Image.open(BytesIO(raw)) as source:
            width, height = source.size
            if width > MAX_IMAGE_WIDTH or height > MAX_IMAGE_HEIGHT:
                raise ValueError("avatar payload is invalid")
            source.load()
            return source.convert("RGB")
    except (binascii.Error, OSError, ValueError, Image.DecompressionBombError) as error:
        raise ValueError("avatar payload is invalid") from error


def _text(value: object, name: str) -> str:
    """Return a normalized text value."""
    if not isinstance(value, str) or not value or len(value) > 128:
        raise ValueError(f"{name} must be a non-empty short string")
    return value


def _nonnegative_int(value: object) -> int:
    """Validate and return a nonnegative integer field."""
    if isinstance(value, bool):
        raise ValueError("integer required")
    parsed = int(value)
    if parsed < 0:
        raise ValueError("non-negative integer required")
    return parsed


def _positive_int(value: object) -> int:
    """Validate and return a positive integer field."""
    parsed = _nonnegative_int(value)
    if parsed == 0:
        raise ValueError("positive integer required")
    return parsed

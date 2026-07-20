from __future__ import annotations

from io import BytesIO

from PIL import Image

from battlevive_bot.images import CARD_H
from battlevive_bot.images import CARD_W
from battlevive_bot.images import build_card


def test_build_card_returns_valid_png_with_expected_dimensions() -> None:
    avatar = Image.new("RGB", (128, 128), (24, 128, 180))

    png = build_card(
        avatar=avatar,
        display_name="PlayerOne",
        rank_current="Silver",
        rank_next="Gold",
        mmr_current=1500,
        mmr_required=2000,
        wins=12,
        losses=8,
    )

    assert png.startswith(b"\x89PNG")
    with Image.open(BytesIO(png)) as image:
        assert image.format == "PNG"
        assert image.size == (CARD_W, CARD_H)
        assert image.mode == "RGB"

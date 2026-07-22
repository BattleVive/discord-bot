from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image
from PIL import ImageDraw
from PIL import ImageFont
from PIL import ImageOps

from .settings import ASSETS_DIR


# Asset paths
FONT_BOLD = str(ASSETS_DIR / "fonts" / "LiberationMono-Bold.ttf")
FONT_REGULAR = str(ASSETS_DIR / "fonts" / "LiberationMono-Regular.ttf")
RANK_ICON_DIR = ASSETS_DIR / "images" / "Battlevive Rank Icons"

RANK_STYLES: dict[str, tuple[str, str]] = {
    "Bronze": ("Bronze.png", "#C87E45"),
    "Silver": ("Silver.png", "#DCE3E8"),
    "Gold": ("Gold.png", "#FFD22A"),
    "Platinum": ("Platinum.png", "#A6E7EC"),
    "Diamond": ("Diamond.png", "#31C9F0"),
    "BATTLEVIVE": ("Grand Champion.png", "#FF4F8B"),
}


# Card dimensions
CARD_W = 640
CARD_H = 220
CORNER_R = 12
PAD = 16
AVATAR_SIZE = 126
CONTENT_X = 172
CONTENT_RIGHT = CARD_W - PAD
BAR_H = 10


# Battlevive palette
C_BG = "#04101c"
C_PANEL = "#2b171d"
C_PANEL_EDGE = "#602938"
C_CRIMSON = "#dd2254"
C_BLUE = "#237ee7"
C_TEXT = "#f7e9ec"
C_MUTED = "#e8b0bf"
C_BAR_BG = "#172638"
C_GREEN = "#3ce68b"
C_RED = "#ff5470"


def _load_fonts() -> dict[str, ImageFont.FreeTypeFont]:
    """Load bundled fonts, raising immediately when an asset is unavailable."""
    return {
        "name": ImageFont.truetype(FONT_BOLD, 24),
        "rank": ImageFont.truetype(FONT_BOLD, 14),
        "label": ImageFont.truetype(FONT_REGULAR, 12),
        "mmr": ImageFont.truetype(FONT_REGULAR, 13),
        "stat": ImageFont.truetype(FONT_BOLD, 20),
    }


def _text_width(font: ImageFont.FreeTypeFont, text: str) -> int:
    bounds = font.getbbox(text)
    return bounds[2] - bounds[0]


def _ellipsize(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> str:
    if _text_width(font, text) <= max_width:
        return text

    ellipsis = "..."
    available = max_width - _text_width(font, ellipsis)
    if available <= 0:
        return ellipsis

    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if _text_width(font, text[:middle]) <= available:
            low = middle
        else:
            high = middle - 1
    return text[:low] + ellipsis


def _circle_crop(img: Image.Image) -> Image.Image:
    total = AVATAR_SIZE + 10
    result = Image.new("RGBA", (total, total), (0, 0, 0, 0))
    fitted = ImageOps.fit(
        img.convert("RGBA"),
        (AVATAR_SIZE, AVATAR_SIZE),
        method=Image.Resampling.LANCZOS,
    )
    mask = Image.new("L", (AVATAR_SIZE, AVATAR_SIZE), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, AVATAR_SIZE - 1, AVATAR_SIZE - 1), fill=255)
    fitted.putalpha(mask)
    result.alpha_composite(fitted, (5, 5))

    # Draw the border last so the avatar cannot obscure it.
    result_draw = ImageDraw.Draw(result)
    result_draw.ellipse(
        (0, 0, total - 1, total - 1),
        outline=C_CRIMSON,
        width=4,
    )
    return result


def _rank_icon(rank: str, size: int) -> Image.Image | None:
    style = RANK_STYLES.get(rank)
    if style is None:
        return None

    with Image.open(Path(RANK_ICON_DIR) / style[0]) as source:
        icon = source.convert("RGBA")
        icon.thumbnail((size, size), Image.Resampling.LANCZOS)
    return icon


def _draw_rank(
    card: Image.Image,
    draw: ImageDraw.ImageDraw,
    fonts: dict[str, ImageFont.FreeTypeFont],
    x: int,
    y: int,
    rank: str,
) -> int:
    icon = _rank_icon(rank, 32)
    if icon is not None:
        card.alpha_composite(icon, (x, y + (32 - icon.height) // 2))
        x += icon.width + 7

    color = RANK_STYLES.get(rank, ("", C_TEXT))[1]
    draw.text((x, y + 8), rank, font=fonts["rank"], fill=color)
    return x + _text_width(fonts["rank"], rank)


def _gradient_bar(width: int, height: int) -> Image.Image:
    gradient = Image.new("RGB", (max(width, 1), height))
    pixels = gradient.load()
    start = (221, 34, 84)
    end = (35, 126, 231)
    for x in range(max(width, 1)):
        amount = x / max(width - 1, 1)
        color = tuple(
            round(start[channel] + (end[channel] - start[channel]) * amount)
            for channel in range(3)
        )
        for y in range(height):
            pixels[x, y] = color
    return gradient


def build_card(
    avatar: Image.Image,
    display_name: str,
    rank_current: str,
    rank_next: str,
    mmr_current: int,
    mmr_required: int,
    wins: int,
    losses: int,
) -> bytes:
    fonts = _load_fonts()
    total_games = wins + losses
    win_rate = round(wins / total_games * 100) if total_games > 0 else 0
    raw_progress = mmr_current / mmr_required if mmr_required > 0 else 0.0
    progress = min(1.0, max(0.0, raw_progress))
    is_max_rank = rank_current == "BATTLEVIVE" and rank_next == rank_current

    card = Image.new("RGBA", (CARD_W, CARD_H), C_BG)
    draw = ImageDraw.Draw(card)
    draw.rounded_rectangle(
        (0, 0, CARD_W - 1, CARD_H - 1),
        radius=CORNER_R,
        fill=C_BG,
        outline=C_PANEL_EDGE,
        width=2,
    )
    avatar_image = _circle_crop(avatar)
    card.alpha_composite(avatar_image, (22, 30))

    name = _ellipsize(display_name, fonts["name"], 315)
    draw.text((CONTENT_X, 15), name, font=fonts["name"], fill=C_TEXT)
    current_width = _text_width(fonts["rank"], rank_current)
    current_color = RANK_STYLES.get(rank_current, ("", C_TEXT))[1]
    draw.text(
        (CONTENT_RIGHT - current_width, 22),
        rank_current,
        font=fonts["rank"],
        fill=current_color,
    )

    progression_y = 48
    next_x = _draw_rank(card, draw, fonts, CONTENT_X, progression_y, rank_current)
    if is_max_rank:
        max_text = "MAX RANK"
        draw.text(
            (CONTENT_RIGHT - _text_width(fonts["rank"], max_text), progression_y + 8),
            max_text,
            font=fonts["rank"],
            fill=C_CRIMSON,
        )
    else:
        draw.text((next_x + 12, progression_y + 7), "→", font=fonts["rank"], fill=C_MUTED)
        _draw_rank(card, draw, fonts, next_x + 40, progression_y, rank_next)

    mmr_y = 91
    draw.text((CONTENT_X, mmr_y), "CURRENT MMR", font=fonts["label"], fill=C_MUTED)
    required_label = "MAXIMUM" if is_max_rank else "NEXT RANK"
    draw.text(
        (CONTENT_RIGHT - _text_width(fonts["label"], required_label), mmr_y),
        required_label,
        font=fonts["label"],
        fill=C_MUTED,
    )
    current_mmr = f"{mmr_current:,} MMR"
    required_mmr = "MAX RANK" if is_max_rank else f"{mmr_required:,} MMR"
    draw.text((CONTENT_X, 106), current_mmr, font=fonts["mmr"], fill=C_TEXT)
    draw.text(
        (CONTENT_RIGHT - _text_width(fonts["mmr"], required_mmr), 106),
        required_mmr,
        font=fonts["mmr"],
        fill=C_TEXT,
    )

    bar_y = 128
    bar_width = CONTENT_RIGHT - CONTENT_X
    draw.rounded_rectangle(
        (CONTENT_X, bar_y, CONTENT_RIGHT, bar_y + BAR_H),
        radius=BAR_H // 2,
        fill=C_BAR_BG,
        outline=C_PANEL_EDGE,
    )
    fill_width = round(bar_width * progress)
    if fill_width > 0:
        gradient = _gradient_bar(fill_width, BAR_H)
        mask = Image.new("L", (fill_width, BAR_H), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            (0, 0, fill_width - 1, BAR_H - 1),
            radius=BAR_H // 2,
            fill=255,
        )
        card.paste(gradient, (CONTENT_X, bar_y), mask)

    stats_top = 151
    stats_bottom = 204
    section_width = (CONTENT_RIGHT - CONTENT_X) // 3
    draw.rounded_rectangle(
        (CONTENT_X, stats_top, CONTENT_RIGHT, stats_bottom),
        radius=7,
        fill=C_PANEL,
        outline=C_PANEL_EDGE,
    )
    for index in (1, 2):
        x = CONTENT_X + section_width * index
        draw.line((x, stats_top + 8, x, stats_bottom - 8), fill=C_PANEL_EDGE, width=1)

    stats = (
        ("WINS", f"{wins:,}", C_GREEN),
        ("LOSSES", f"{losses:,}", C_RED),
        ("WIN RATE", f"{win_rate}%", C_TEXT),
    )
    for index, (label, value, color) in enumerate(stats):
        center = CONTENT_X + section_width * index + section_width // 2
        draw.text(
            (center - _text_width(fonts["label"], label) // 2, stats_top + 6),
            label,
            font=fonts["label"],
            fill=C_MUTED,
        )
        draw.text(
            (center - _text_width(fonts["stat"], value) // 2, stats_top + 23),
            value,
            font=fonts["stat"],
            fill=color,
        )

    output = BytesIO()
    card.convert("RGB").save(output, format="PNG")
    return output.getvalue()

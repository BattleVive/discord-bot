from __future__ import annotations

from io import BytesIO

# pyrefly: ignore [missing-import]
from PIL import Image
# pyrefly: ignore [missing-import]
from PIL import ImageDraw
# pyrefly: ignore [missing-import]
from PIL import ImageFont

from .settings import ASSETS_DIR


# Asset paths
FONT_BOLD = str(ASSETS_DIR / "fonts" / "DejaVuSans-Bold.ttf")
FONT_REGULAR = str(ASSETS_DIR / "fonts" / "DejaVuSans.ttf")


# Card dimensions
CARD_W = 480
CARD_H = 130
PAD = 16
PFP_SIZE = 58
PFP_BORDER = 2
CONTENT_X = PAD + PFP_SIZE + 14
BAR_H = 8
CORNER_R = 8


# Palette
C_BG = (30, 33, 40)
C_BORDER = (58, 61, 69)
C_TEXT = (242, 243, 245)
C_MUTED = (128, 132, 142)
C_SECONDARY = (181, 186, 193)
C_ACCENT = (64, 181, 184)
C_ACCENT2 = (126, 182, 255)
C_BAR_BG = (46, 51, 64)
C_WIN = (87, 242, 135)
C_GOLD = (255, 193, 7)
C_GOLD_TEXT = (26, 26, 0)
C_BADGE_NEXT = (37, 42, 58)
C_NEXT_TEXT = (126, 182, 255)
C_NEXT_BORDER = (58, 85, 128)
C_ARROW = (92, 96, 112)

STAR = "\u2605"
ARROW_RIGHT = "\u2192"
ARROW_UP = "\u2191"
MIDDLE_DOT = "\u00b7"


def _load_fonts() -> dict[str, ImageFont.FreeTypeFont]:
    return {
        "username": ImageFont.truetype(FONT_BOLD, 15),
        "badge": ImageFont.truetype(FONT_BOLD, 11),
        "meta": ImageFont.truetype(FONT_REGULAR, 10),
        "stats": ImageFont.truetype(FONT_REGULAR, 12),
        "stats_b": ImageFont.truetype(FONT_BOLD, 12),
        "arrow": ImageFont.truetype(FONT_REGULAR, 13),
    }


def _circle_crop(
    img: Image.Image,
    size: int,
    border_color: tuple[int, int, int],
    border_w: int,
) -> Image.Image:
    total = size + border_w * 2
    result = Image.new("RGBA", (total, total), (0, 0, 0, 0))
    ImageDraw.Draw(result).ellipse([0, 0, total - 1, total - 1], fill=border_color)

    avatar = img.convert("RGBA").resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, size - 1, size - 1], fill=255)
    avatar.putalpha(mask)

    result.paste(avatar, (border_w, border_w), avatar)
    return result


def _gradient_bar(
    width: int,
    height: int,
    c1: tuple[int, int, int],
    c2: tuple[int, int, int],
) -> Image.Image:
    row = Image.new("RGB", (width, 1))
    px = row.load()
    for x in range(width):
        t = x / max(width - 1, 1)
        px[x, 0] = tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))
    return row.resize((width, height), Image.NEAREST)


def _text_w(font: ImageFont.FreeTypeFont, text: str) -> int:
    bb = font.getbbox(text)
    return bb[2] - bb[0]


def _draw_badge(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int],
    text_color: tuple[int, int, int],
    outline: tuple[int, int, int] | None = None,
    pad_x: int = 9,
    pad_y: int = 3,
) -> int:
    bb = font.getbbox(text)
    tw = bb[2] - bb[0]
    th = bb[3] - bb[1]
    bw = tw + pad_x * 2
    bh = th + pad_y * 2
    draw.rounded_rectangle(
        [x, y, x + bw, y + bh],
        radius=bh // 2,
        fill=fill,
        outline=outline,
        width=1 if outline else 0,
    )
    draw.text((x + pad_x - bb[0], y + pad_y - bb[1]), text, font=font, fill=text_color)
    return bw


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
    total = wins + losses
    win_rate = round(wins / total * 100) if total > 0 else 0
    mmr_pct = mmr_current / mmr_required

    card = Image.new("RGBA", (CARD_W, CARD_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(card)
    draw.rounded_rectangle(
        [0, 0, CARD_W - 1, CARD_H - 1],
        radius=CORNER_R,
        fill=C_BG,
        outline=C_BORDER,
        width=1,
    )

    pfp = _circle_crop(avatar, PFP_SIZE, C_ACCENT, PFP_BORDER)
    pfp_y = (CARD_H - pfp.height) // 2
    card.paste(pfp, (PAD, pfp_y), pfp)

    cx = CONTENT_X
    cy = PAD
    bar_w = CARD_W - cx - PAD

    draw.text((cx, cy), display_name, font=fonts["username"], fill=C_TEXT)
    cy += fonts["username"].getbbox(display_name)[3] + 8

    x_cur = cx
    x_cur += _draw_badge(
        draw,
        x_cur,
        cy,
        f"{STAR} {rank_current}",
        fonts["badge"],
        fill=C_GOLD,
        text_color=C_GOLD_TEXT,
    ) + 6
    draw.text((x_cur, cy + 3), ARROW_RIGHT, font=fonts["arrow"], fill=C_ARROW)
    x_cur += _text_w(fonts["arrow"], ARROW_RIGHT) + 6
    _draw_badge(
        draw,
        x_cur,
        cy,
        rank_next,
        fonts["badge"],
        fill=C_BADGE_NEXT,
        text_color=C_NEXT_TEXT,
        outline=C_NEXT_BORDER,
    )
    cy += fonts["badge"].getbbox("A")[3] + 6 + 9

    mmr_left = f"{mmr_current:,} MMR"
    mmr_mid = f"{round(mmr_pct * 100)}%"
    mmr_right = f"{mmr_required:,} MMR"
    draw.text((cx, cy), mmr_left, font=fonts["meta"], fill=C_MUTED)
    draw.text(
        (cx + bar_w // 2 - _text_w(fonts["meta"], mmr_mid) // 2, cy),
        mmr_mid,
        font=fonts["meta"],
        fill=C_ACCENT,
    )
    draw.text(
        (cx + bar_w - _text_w(fonts["meta"], mmr_right), cy),
        mmr_right,
        font=fonts["meta"],
        fill=C_MUTED,
    )
    cy += 13

    draw.rounded_rectangle(
        [cx, cy, cx + bar_w, cy + BAR_H],
        radius=BAR_H // 2,
        fill=C_BAR_BG,
    )
    fill_w = max(1, int(bar_w * mmr_pct))
    grad = _gradient_bar(fill_w, BAR_H, C_ACCENT, C_ACCENT2)
    fill_mask = Image.new("L", (fill_w, BAR_H), 0)
    ImageDraw.Draw(fill_mask).rounded_rectangle(
        [0, 0, fill_w + BAR_H, BAR_H - 1],
        radius=BAR_H // 2,
        fill=255,
    )
    card.paste(grad, (cx, cy), fill_mask)
    cy += BAR_H + 9

    win_rate_text = f"{ARROW_UP} {win_rate}% WR"
    win_loss_text = f"W {wins} {MIDDLE_DOT} L {losses}"
    draw.text((cx, cy), win_rate_text, font=fonts["stats_b"], fill=C_WIN)
    draw.text(
        (cx + _text_w(fonts["stats_b"], win_rate_text) + 14, cy),
        win_loss_text,
        font=fonts["stats"],
        fill=C_SECONDARY,
    )

    out = BytesIO()
    card.convert("RGB").save(out, format="PNG")
    return out.getvalue()

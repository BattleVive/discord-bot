from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image
from PIL import ImageColor
import pytest

from battlevive_bot.images import _ellipsize
from battlevive_bot.images import _load_fonts
from battlevive_bot.images import _rank_icon
from battlevive_bot.images import _text_width
from battlevive_bot.images import CARD_H
from battlevive_bot.images import CARD_W
from battlevive_bot.images import C_CRIMSON
from battlevive_bot.images import C_PANEL_EDGE
from battlevive_bot.images import FONT_BOLD
from battlevive_bot.images import FONT_REGULAR
from battlevive_bot.images import RANK_ICON_DIR
from battlevive_bot.images import RANK_STYLES
from battlevive_bot.images import build_card


ROOT_DIR = Path(__file__).resolve().parents[1]
AVATAR = Image.new("RGBA", (160, 120), (24, 128, 180, 210))
EXPECTED_RANK_STYLES = {
    "Bronze": ("Bronze.png", "#C87E45"),
    "Silver": ("Silver.png", "#DCE3E8"),
    "Gold": ("Gold.png", "#FFD22A"),
    "Platinum": ("Platinum.png", "#A6E7EC"),
    "Diamond": ("Diamond.png", "#31C9F0"),
    "BATTLEVIVE": ("Grand Champion.png", "#FF4F8B"),
}


def make_card(**overrides: object) -> bytes:
    arguments: dict[str, object] = {
        "avatar": AVATAR,
        "display_name": "PlayerOne",
        "rank_current": "Silver",
        "rank_next": "Gold",
        "mmr_current": 1500,
        "mmr_required": 2000,
        "wins": 12,
        "losses": 8,
    }
    arguments.update(overrides)
    return build_card(**arguments)  # type: ignore[arg-type]


def assert_valid_card(png: bytes) -> None:
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    with Image.open(BytesIO(png)) as image:
        assert image.format == "PNG"
        assert image.size == (640, 220) == (CARD_W, CARD_H)
        assert image.mode == "RGB"


def test_build_card_returns_valid_rgb_png() -> None:
    assert_valid_card(make_card())


def test_card_uses_a_normal_border_without_accent_strips() -> None:
    with Image.open(BytesIO(make_card())) as image:
        assert image.getpixel((0, CARD_H // 2)) == ImageColor.getrgb(C_PANEL_EDGE)
        assert image.getpixel((CARD_W - 1, CARD_H // 2)) == ImageColor.getrgb(
            C_PANEL_EDGE
        )


def test_avatar_has_only_a_crimson_border() -> None:
    with Image.open(BytesIO(make_card())) as image:
        assert image.getpixel((22, 30 + 68)) == ImageColor.getrgb(C_CRIMSON)


def test_bundled_liberation_mono_weights_load() -> None:
    fonts = _load_fonts()

    assert Path(FONT_REGULAR).name == "LiberationMono-Regular.ttf"
    assert Path(FONT_BOLD).name == "LiberationMono-Bold.ttf"
    assert "Liberation Mono" in fonts["label"].getname()[0]
    assert fonts["label"].getname()[1] == "Regular"
    assert "Liberation Mono" in fonts["name"].getname()[0]
    assert fonts["name"].getname()[1] == "Bold"


def test_rank_styles_cover_every_model_rank_and_expected_icon() -> None:
    assert RANK_STYLES == EXPECTED_RANK_STYLES

    for rank, (filename, _) in EXPECTED_RANK_STYLES.items():
        assert (RANK_ICON_DIR / filename).is_file()
        assert_valid_card(
            make_card(
                rank_current=rank,
                rank_next="BATTLEVIVE" if rank == "Diamond" else "Silver",
            )
        )

    assert "Champion.png" not in {filename for filename, _ in RANK_STYLES.values()}


@pytest.mark.parametrize("rank", EXPECTED_RANK_STYLES)
def test_rank_icons_preserve_aspect_ratio_and_transparency(rank: str) -> None:
    source_path = RANK_ICON_DIR / EXPECTED_RANK_STYLES[rank][0]
    with Image.open(source_path) as source:
        source_ratio = source.width / source.height

    icon = _rank_icon(rank, 32)

    assert icon is not None
    assert icon.mode == "RGBA"
    assert max(icon.size) <= 32
    assert icon.width / icon.height == pytest.approx(source_ratio, rel=0.03)


def test_unknown_future_rank_renders_without_an_icon() -> None:
    assert _rank_icon("Mythic", 32) is None
    assert_valid_card(make_card(rank_current="Mythic", rank_next="Ascendant"))


@pytest.mark.parametrize(
    ("overrides"),
    [
        {"wins": 0, "losses": 0},
        {
            "rank_current": "BATTLEVIVE",
            "rank_next": "BATTLEVIVE",
            "mmr_current": 9000,
            "mmr_required": 9000,
        },
        {"display_name": "A very long display name that cannot possibly fit on the card"},
        {"wins": 12_345_678, "losses": 98_765_432, "mmr_current": 12_345_678},
        {"mmr_current": -500, "mmr_required": 1000},
        {"mmr_current": 5000, "mmr_required": 1000},
        {"mmr_current": 5000, "mmr_required": 0},
    ],
)
def test_edge_case_cards_render(overrides: dict[str, object]) -> None:
    assert_valid_card(make_card(**overrides))


def test_long_names_are_ellipsized_to_the_available_width() -> None:
    font = _load_fonts()["name"]
    result = _ellipsize("Player name " * 20, font, 315)

    assert result.endswith("...")
    assert _text_width(font, result) <= 315


def test_font_license_and_readme_attribution() -> None:
    license_path = ROOT_DIR / "app/assets/fonts/LiberationMono-LICENSE.txt"
    readme = (ROOT_DIR / "README.md").read_text(encoding="utf-8")

    assert license_path.is_file()
    assert "SIL OPEN FONT LICENSE Version 1.1" in license_path.read_text(
        encoding="utf-8"
    )
    assert "Liberation Mono 2.1.5" in readme
    assert "SIL Open Font License 1.1" in readme
    assert "app/assets/fonts/LiberationMono-LICENSE.txt" in readme
    assert "github.com/liberationfonts/liberation-fonts" in readme


def test_no_legacy_font_assets_or_references_remain() -> None:
    legacy_name = "deja" + "vu"
    text_suffixes = {".md", ".py", ".txt", ".yml", ".yaml"}
    references = []
    for path in ROOT_DIR.rglob("*"):
        if any(part in {".git", ".venv", "__pycache__"} for part in path.parts):
            continue
        if path.is_file() and path.suffix in text_suffixes:
            if legacy_name in path.read_text(encoding="utf-8", errors="ignore").lower():
                references.append(path)

    assert not list((ROOT_DIR / "app/assets/fonts").glob("*Deja" + "Vu*"))
    assert references == []

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image
from PIL import ImageColor
from PIL import ImageChops
from PIL import ImageDraw
from PIL import ImageFont
import pytest

from battlevive_bot.images import _ellipsize
from battlevive_bot.images import _load_fonts
from battlevive_bot.images import _rank_icon
from battlevive_bot.images import _text_width
from battlevive_bot.images import CARD_H
from battlevive_bot.images import CARD_W
from battlevive_bot.images import LEADERBOARD_ENTRY_H
from battlevive_bot.images import LEADERBOARD_HEADER_H
from battlevive_bot.images import LEADERBOARD_NAME_X
from battlevive_bot.images import LEADERBOARD_NAME_WIDTH
from battlevive_bot.images import LEADERBOARD_RANK_X
from battlevive_bot.images import LEADERBOARD_STATS_X
from battlevive_bot.images import LEADERBOARD_W
from battlevive_bot.images import C_BG
from battlevive_bot.images import C_CRIMSON
from battlevive_bot.images import C_PANEL_EDGE
from battlevive_bot.images import FONT_BOLD
from battlevive_bot.images import FONT_REGULAR
from battlevive_bot.images import RANK_ICON_DIR
from battlevive_bot.images import RANK_STYLES
from battlevive_bot.images import build_card
from battlevive_bot.images import build_leaderboard_png
from battlevive_bot.images import LeaderboardEntry


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
    license_path = ROOT_DIR / "app/assets/fonts/LICENSE"
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


def make_leaderboard_entry(**overrides: object) -> LeaderboardEntry:
    values: dict[str, object] = {
        "place": 1,
        "username": "PlayerOne",
        "rank": "Silver",
        "mmr": 1500,
        "wins": 12,
        "losses": 8,
        "win_rate": 60.0,
    }
    values.update(overrides)
    return LeaderboardEntry(**values)  # type: ignore[arg-type]


def assert_valid_leaderboard_png(png: bytes, size: tuple[int, int]) -> None:
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    with Image.open(BytesIO(png)) as image:
        assert image.format == "PNG"
        assert image.size == size
        assert image.mode == "RGB"


@pytest.mark.parametrize("count", [0, 1, 50])
def test_leaderboard_is_one_wide_png_with_a_fixed_row_height(count: int) -> None:
    entries = [make_leaderboard_entry(place=index + 1) for index in range(count)]

    rendered = build_leaderboard_png(entries, "Spring 2026")

    assert_valid_leaderboard_png(
        rendered,
        (LEADERBOARD_W, LEADERBOARD_HEADER_H + count * LEADERBOARD_ENTRY_H),
    )
    assert LEADERBOARD_W == 1280
    assert LEADERBOARD_HEADER_H == 184
    assert LEADERBOARD_ENTRY_H == 124


def test_leaderboard_uses_fixed_name_rank_and_stat_regions() -> None:
    entry = make_leaderboard_entry(
        username="A very long username that must be clipped before the rank column",
        rank="BATTLEVIVE",
    )

    rendered = build_leaderboard_png([entry], "Season 1")
    assert_valid_leaderboard_png(
        rendered,
        (LEADERBOARD_W, LEADERBOARD_HEADER_H + LEADERBOARD_ENTRY_H),
    )

    # These are layout invariants, rather than a font-rasterization snapshot.
    assert (LEADERBOARD_NAME_X, LEADERBOARD_NAME_WIDTH) == (112, 408)
    assert LEADERBOARD_RANK_X == 544
    assert LEADERBOARD_STATS_X == 816
    fonts = _load_fonts()
    clipped = _ellipsize(entry.username, fonts["leaderboard_username"], 408)
    assert clipped.endswith("...")
    assert _text_width(fonts["leaderboard_username"], clipped) <= 408


def test_username_and_rank_labels_share_the_same_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    positions: dict[str, tuple[tuple[int, int], object]] = {}
    composites: list[tuple[tuple[int, int], tuple[int, int]]] = []
    original_text = ImageDraw.ImageDraw.text
    original_composite = Image.Image.alpha_composite

    def capture_text(
        draw: ImageDraw.ImageDraw,
        position: tuple[int, int],
        text: str,
        *args: object,
        **kwargs: object,
    ) -> None:
        if text in {"1.", "PlayerOne", "Silver", "MMR", "1,500"}:
            positions[text] = (position, kwargs.get("anchor"))
        original_text(draw, position, text, *args, **kwargs)

    def capture_composite(
        image: Image.Image,
        source: Image.Image,
        destination: tuple[int, int] = (0, 0),
        source_box: tuple[int, int] = (0, 0),
    ) -> None:
        if destination[0] >= LEADERBOARD_RANK_X:
            composites.append((destination, source.size))
        original_composite(image, source, destination, source_box)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", capture_text)
    monkeypatch.setattr(Image.Image, "alpha_composite", capture_composite)

    build_leaderboard_png([make_leaderboard_entry()], "Season 1")

    row_center = LEADERBOARD_HEADER_H + LEADERBOARD_ENTRY_H // 2
    assert positions["PlayerOne"][0][1] == positions["Silver"][0][1]
    assert positions["PlayerOne"] == ((LEADERBOARD_NAME_X, row_center), "lm")
    assert positions["Silver"][0][1] == row_center
    assert positions["Silver"][1] == "lm"
    assert positions["1."] == ((88, row_center), "rm")
    assert positions["MMR"][0][0] == positions["1,500"][0][0]
    assert positions["MMR"][1] == positions["1,500"][1] == "mm"

    assert len(composites) == 1
    icon_position, icon_size = composites[0]
    assert icon_position[1] + icon_size[1] / 2 == pytest.approx(row_center, abs=0.5)


def test_leaderboard_has_one_rounded_outer_frame_and_square_row_separators() -> None:
    rendered = build_leaderboard_png(
        [
            make_leaderboard_entry(place=1),
            make_leaderboard_entry(place=2),
        ],
        "Season 1",
    )
    edge = ImageColor.getrgb(C_PANEL_EDGE)
    background = ImageColor.getrgb(C_BG)

    with Image.open(BytesIO(rendered)) as image:
        first_row_top = LEADERBOARD_HEADER_H
        second_row_top = LEADERBOARD_HEADER_H + LEADERBOARD_ENTRY_H

        # The internal separators reach the straight outer sides without
        # creating rounded corners around either individual row.
        assert image.getpixel((4, first_row_top)) == edge
        assert image.getpixel((LEADERBOARD_W - 5, first_row_top)) == edge
        assert image.getpixel((4, second_row_top)) == edge
        assert image.getpixel((LEADERBOARD_W - 5, second_row_top)) == edge
        assert image.getpixel((0, first_row_top + 5)) == edge
        assert image.getpixel((0, second_row_top + 5)) == edge
        assert image.getpixel((4, second_row_top - 5)) == background

        # Only the four corners of the complete image are rounded.
        assert image.getpixel((0, 0)) == background
        assert image.getpixel((0, image.height - 1)) == background


@pytest.mark.parametrize("rank", [*EXPECTED_RANK_STYLES, "Mythic"])
def test_wide_leaderboard_supports_known_and_unknown_ranks(rank: str) -> None:
    rendered = build_leaderboard_png([make_leaderboard_entry(rank=rank)], "Season 1")

    assert_valid_leaderboard_png(
        rendered,
        (LEADERBOARD_W, LEADERBOARD_HEADER_H + LEADERBOARD_ENTRY_H),
    )


def test_wide_leaderboard_fits_long_values_and_zero_matches() -> None:
    rendered = build_leaderboard_png(
        [
            make_leaderboard_entry(
                username="A very long username that must be clipped to fit the leaderboard",
                rank="BATTLEVIVE",
                mmr=12_345_678,
                wins=0,
                losses=0,
                win_rate=0,
            ),
            make_leaderboard_entry(
                place=2,
                username="Large values",
                rank="Mythic",
                mmr=999_999_999,
                wins=987_654_321,
                losses=123_456_789,
                win_rate=88.9,
            ),
        ],
        "Season 1",
    )

    assert_valid_leaderboard_png(
        rendered,
        (LEADERBOARD_W, LEADERBOARD_HEADER_H + 2 * LEADERBOARD_ENTRY_H),
    )


def test_extreme_stat_values_cannot_overflow_their_columns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    drawn_values: list[tuple[str, ImageFont.FreeTypeFont]] = []
    original_text = ImageDraw.ImageDraw.text

    def capture_text(
        draw: ImageDraw.ImageDraw,
        position: tuple[int, int],
        text: str,
        *args: object,
        **kwargs: object,
    ) -> None:
        if "," in text or text.endswith("%"):
            drawn_values.append((text, kwargs["font"]))  # type: ignore[arg-type]
        original_text(draw, position, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", capture_text)
    enormous = 10**100

    build_leaderboard_png(
        [
            make_leaderboard_entry(
                mmr=enormous,
                wins=enormous,
                losses=enormous,
                win_rate=float(enormous),
            )
        ],
        "Season 1",
    )

    assert len(drawn_values) == 4
    assert all(_text_width(font, text) <= 102 for text, font in drawn_values)


def test_wide_leaderboard_can_redraw_one_row_on_an_existing_complete_image() -> None:
    entries = [
        make_leaderboard_entry(place=1, username="Alpha", mmr=2500),
        make_leaderboard_entry(place=2, username="Beta", mmr=2400),
    ]
    original = build_leaderboard_png(entries, "Season 3")
    updated_entries = [
        entries[0],
        make_leaderboard_entry(place=2, username="Beta", mmr=2600),
    ]

    partial = build_leaderboard_png(
        updated_entries,
        "Season 3",
        base_image=original,
        redraw_slots={2},
    )
    fully_redrawn = build_leaderboard_png(updated_entries, "Season 3")

    with (
        Image.open(BytesIO(original)) as before,
        Image.open(BytesIO(partial)) as after,
        Image.open(BytesIO(fully_redrawn)) as expected,
    ):
        unchanged = ImageChops.difference(
            before.crop(
                (
                    0,
                    0,
                    LEADERBOARD_W,
                    LEADERBOARD_HEADER_H + LEADERBOARD_ENTRY_H,
                )
            ),
            after.crop(
                (
                    0,
                    0,
                    LEADERBOARD_W,
                    LEADERBOARD_HEADER_H + LEADERBOARD_ENTRY_H,
                )
            ),
        )
        changed = ImageChops.difference(
            before.crop(
                (
                    0,
                    LEADERBOARD_HEADER_H + LEADERBOARD_ENTRY_H,
                    LEADERBOARD_W,
                    after.height,
                )
            ),
            after.crop(
                (
                    0,
                    LEADERBOARD_HEADER_H + LEADERBOARD_ENTRY_H,
                    LEADERBOARD_W,
                    after.height,
                )
            ),
        )
        assert unchanged.getbbox() is None
        assert changed.getbbox() is not None
        assert ImageChops.difference(after, expected).getbbox() is None


def test_wide_leaderboard_rejects_a_base_image_with_the_wrong_height() -> None:
    with pytest.raises(ValueError, match="expected"):
        build_leaderboard_png(
            [make_leaderboard_entry()],
            "Season 1",
            base_image=build_leaderboard_png([], "Season 1"),
            redraw_slots={1},
        )

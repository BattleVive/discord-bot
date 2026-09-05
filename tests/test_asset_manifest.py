from __future__ import annotations

import json
from pathlib import Path
import re

from PIL import Image
import pytest

from battlevive_bot.asset_manifest import MAP_MANIFEST_PATH
from battlevive_bot.asset_manifest import resolve_map_asset


@pytest.fixture(scope="module")
def map_entries() -> list[dict[str, object]]:
    data = json.loads(MAP_MANIFEST_PATH.read_text(encoding="utf-8"))
    return data["maps"]


def test_every_map_alias_resolves_to_existing_day_asset(
    map_entries: list[dict[str, object]],
) -> None:
    for entry in map_entries:
        for alias in [entry["name"], *entry["aliases"]]:
            resolved = resolve_map_asset(alias)
            assert resolved is not None
            assert resolved.name == entry["name"]
            assert resolved.variant == "day"
            assert resolved.path.is_file()


def test_map_resolver_handles_punctuation_variants_and_unknown_maps() -> None:
    blackstone = resolve_map_asset("  BLACKSTONE-ARENA  ")
    assert blackstone is not None
    assert blackstone.name == "Blackstone Arena"
    night = resolve_map_asset("Misty_Woods_Night")
    assert night is not None
    assert night.variant == "night"
    assert night.path.name == "misty-woods-night.png"
    assert resolve_map_asset("Unknown Arena") is None
    assert resolve_map_asset(None) is None


def test_map_resolver_returns_text_only_fallback_for_missing_asset(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "maps": [
                    {
                        "name": "Missing Map",
                        "aliases": ["Missing"],
                        "day": "missing-day.png",
                        "night": "missing-night.png",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    assert resolve_map_asset("Missing", manifest_path=manifest) is None


def test_committed_map_and_emoji_images_have_expected_dimensions(
    map_entries: list[dict[str, object]],
) -> None:
    map_paths = [
        MAP_MANIFEST_PATH.parent / entry[variant]
        for entry in map_entries
        for variant in ("day", "night")
    ]
    assert len(map_paths) == 16
    assert all(Image.open(path).size == (400, 245) for path in map_paths)

    emoji_dir = MAP_MANIFEST_PATH.parent.parent / "emojies" / "champions"
    emoji_paths = sorted(emoji_dir.glob("*.png"))
    assert len(emoji_paths) == 28
    assert all(Image.open(path).size == (128, 128) for path in emoji_paths)
    assert all(path.stat().st_size < 256 * 1024 for path in emoji_paths)
    assert all(re.fullmatch(r"[a-z0-9_-]+", path.stem) is not None for path in emoji_paths)

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any


MAP_MANIFEST_PATH = (
    Path(__file__).resolve().parents[1] / "assets" / "maps" / "manifest.json"
)


@dataclass(frozen=True)
class MapAsset:
    name: str
    variant: str
    path: Path


def normalize_asset_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _load_manifest(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as manifest_file:
        data = json.load(manifest_file)
    maps = data.get("maps") if isinstance(data, dict) else None
    if not isinstance(maps, list):
        raise ValueError("Map asset manifest must contain a maps array")
    return maps


def resolve_map_asset(
    selected_map: str | None,
    variant: str | None = None,
    *,
    manifest_path: Path = MAP_MANIFEST_PATH,
) -> MapAsset | None:
    """Resolve a selected map; base-only values intentionally prefer day."""
    if not selected_map or not selected_map.strip():
        return None

    requested_variant = variant.casefold() if variant else None
    base_name = selected_map
    suffix_match = re.search(r"(?:[\s_-]+)(day|night)\s*$", selected_map, re.I)
    if suffix_match is not None:
        requested_variant = requested_variant or suffix_match.group(1).casefold()
        base_name = selected_map[: suffix_match.start()]
    if requested_variant not in {None, "day", "night"}:
        raise ValueError("Map variant must be day, night, or None")
    requested_variant = requested_variant or "day"

    normalized = normalize_asset_name(base_name)
    for entry in _load_manifest(manifest_path):
        if not isinstance(entry, dict):
            raise ValueError("Map manifest entries must be objects")
        name = entry.get("name")
        aliases = entry.get("aliases")
        asset_path = entry.get(requested_variant)
        if (
            not isinstance(name, str)
            or not isinstance(aliases, list)
            or not all(isinstance(alias, str) for alias in aliases)
            or not isinstance(asset_path, str)
        ):
            raise ValueError("Map manifest entry is malformed")
        known_names = {normalize_asset_name(name)}
        known_names.update(normalize_asset_name(alias) for alias in aliases)
        if normalized in known_names:
            resolved_path = manifest_path.parent / asset_path
            if not resolved_path.is_file():
                return None
            return MapAsset(
                name=name,
                variant=requested_variant,
                path=resolved_path,
            )
    return None

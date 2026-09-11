#!/usr/bin/env python3
"""Validate service-version/content changes and produce a digest reuse plan."""
from __future__ import annotations

import json
import sys


SERVICES = {
    "gateway-service": ("services/gateway/",),
    "upstream-service": ("services/upstream-data/",),
    "image-renderer": ("services/image-renderer/",),
}
SHARED_INPUTS = ("docker-compose.yml", ".dockerignore")
SERVICE_SHARED_INPUTS = {
    "gateway-service": ("init-db/",),
    "upstream-service": (),
    "image-renderer": ("assets/",),
}


def plan(payload: dict) -> dict:
    changed = set(payload["changed_files"])
    versions = payload["versions"]
    previous = payload.get("previous_versions", {})
    existing = payload.get("existing_digests", {})
    result = {"services": {}}
    for name, prefixes in SERVICES.items():
        version_paths = {f"{prefix}VERSION" for prefix in prefixes}
        content_changed = any(
            (path.startswith(prefixes) and path not in version_paths)
            or path in SHARED_INPUTS
            or path.startswith(SERVICE_SHARED_INPUTS[name])
            for path in changed
        )
        version_changed = versions.get(name) != previous.get(name)
        if content_changed and not version_changed:
            raise SystemExit(f"changed image inputs without a service-version bump: {name}")
        if version_changed and not content_changed:
            raise SystemExit(f"service-version bump without changed image content: {name}")
        result["services"][name] = {"version": versions[name], "push": content_changed, "digest": existing.get(name)}
    return result


def main() -> None:
    json.dump(plan(json.load(sys.stdin)), sys.stdout, sort_keys=True)


if __name__ == "__main__":
    main()

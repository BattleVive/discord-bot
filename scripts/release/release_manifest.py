#!/usr/bin/env python3
"""Create or validate the immutable, digest-pinned deployment manifest."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys

SHA = re.compile(r"^sha256:[0-9a-f]{64}$")
SERVICES = {"gateway-service", "upstream-service", "image-renderer"}


def validate(item: dict) -> None:
    """Validate the structure and digests of a release manifest."""
    if set(item.get("services", {})) != SERVICES:
        raise ValueError("manifest must name exactly the three service images")
    if not SHA.fullmatch(item.get("bundle_checksum", "")):
        raise ValueError("manifest bundle_checksum must be sha256 hex")
    if not re.fullmatch(r"[0-9a-f]{40}", item.get("source_revision", "")):
        raise ValueError("manifest source_revision must be a full commit SHA")
    for name, service in item["services"].items():
        if not SHA.fullmatch(service.get("digest", "")):
            raise ValueError(f"{name} has no immutable digest")
        if not re.fullmatch(r"\d+\.\d+\.\d+", service.get("version", "")):
            raise ValueError(f"{name} has an invalid version")


def main() -> None:
    """Run the release manifest command-line entry point."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()
    item = json.load(sys.stdin)
    try:
        validate(item)
    except ValueError as error:
        print(error, file=sys.stderr)
        raise SystemExit(1) from error
    if args.validate:
        return
    encoded = json.dumps(item, sort_keys=True, separators=(",", ":")).encode()
    item["manifest_checksum"] = hashlib.sha256(encoded).hexdigest()
    json.dump(item, sys.stdout, sort_keys=True)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Build and validate a release manifest from immutable OCI digests."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from release_manifest import SERVICES, validate


def service_argument(value: str) -> tuple[str, str, str]:
    """Parse a service name and image digest."""
    try:
        name, version, digest = value.split("=", 2)
    except ValueError as error:
        raise argparse.ArgumentTypeError("service must be NAME=VERSION=sha256:DIGEST") from error
    if name not in SERVICES:
        raise argparse.ArgumentTypeError(f"unknown service: {name}")
    return name, version, digest


def main() -> None:
    """Run the build manifest command-line entry point."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--bot-version", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--service", action="append", type=service_argument, required=True)
    args = parser.parse_args()

    services = {name: {"version": version, "digest": digest} for name, version, digest in args.service}
    item = {
        "bot_version": args.bot_version,
        "bundle_checksum": f"sha256:{hashlib.sha256(args.bundle.read_bytes()).hexdigest()}",
        "source_revision": args.revision,
        "services": services,
    }
    validate(item)
    encoded = json.dumps(item, sort_keys=True, separators=(",", ":")).encode()
    item["manifest_checksum"] = hashlib.sha256(encoded).hexdigest()
    args.output.write_text(json.dumps(item, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Verify a published OCI index supplied on stdin."""

import argparse
import json
import sys


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--revision", required=True)
    args = parser.parse_args()
    manifest = json.load(sys.stdin)

    revision = manifest.get("annotations", {}).get("org.opencontainers.image.revision")
    if revision != args.revision:
        print(f"manifest revision mismatch: expected {args.revision}, got {revision}", file=sys.stderr)
        raise SystemExit(1)

    platforms = {
        (item.get("platform", {}).get("os"), item.get("platform", {}).get("architecture"))
        for item in manifest.get("manifests", [])
    }
    required = {("linux", "amd64"), ("linux", "arm64")}
    missing = required - platforms
    if missing:
        names = ", ".join(f"{os_name}/{architecture}" for os_name, architecture in sorted(missing))
        print(f"manifest is missing required platform(s): {names}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()

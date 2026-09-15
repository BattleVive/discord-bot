#!/usr/bin/env python3
"""Print compatible GHCR tags for one independently versioned service image."""
from __future__ import annotations

import argparse
import re
import sys


VERSION_RE = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


def main() -> None:
    """Print exact, minor, major, and latest tags for a canonical version."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    match = VERSION_RE.fullmatch(args.version)
    if not match:
        parser.error("version must be canonical MAJOR.MINOR.PATCH")
    major, minor, _ = match.groups()
    print(args.version)
    print(f"{major}.{minor}")
    print(major)
    print("latest")


if __name__ == "__main__":
    main()

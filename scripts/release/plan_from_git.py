#!/usr/bin/env python3
"""Build a release-service plan from a checked-out Git revision."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from service_plan import SERVICES, plan


ROOT = Path(__file__).resolve().parents[2]


def git(*args: str) -> str:
    """Run Git and return its standard output."""
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def version_at(revision: str, service: str) -> str:
    """Read a service version at a Git revision."""
    path = SERVICES[service][0] + "VERSION"
    try:
        return subprocess.check_output(
            ["git", "show", f"{revision}:{path}"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except subprocess.CalledProcessError:
        return ""


def main() -> None:
    """Run the plan from git command-line entry point."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", default="HEAD")
    args = parser.parse_args()
    versions = {name: (ROOT / prefixes[0] / "VERSION").read_text().strip() for name, prefixes in SERVICES.items()}
    payload = {
        "changed_files": git("diff", "--name-only", args.base, args.head).splitlines(),
        "versions": versions,
        "previous_versions": {name: version_at(args.base, name) for name in SERVICES},
    }
    json.dump(plan(payload), sys.stdout, sort_keys=True)


if __name__ == "__main__":
    main()

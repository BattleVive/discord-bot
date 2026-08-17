#!/usr/bin/env python3
"""Plan release aliases and production promotion from JSON on stdin."""

import json
import re
import sys


TAG_RE = re.compile(r"^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def fail(message: str, code: int = 2) -> "None":
    print(message, file=sys.stderr)
    raise SystemExit(code)


def parse_tag(tag: str):
    match = TAG_RE.fullmatch(tag)
    if not match:
        fail(f"{tag!r} is not a stable release tag")
    return tuple(int(part) for part in match.groups())


def parse_version(version: str):
    match = re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", version)
    return tuple(int(part) for part in match.groups()) if match else None


def main() -> None:
    payload = json.load(sys.stdin)
    tag = payload.get("tag", "")
    sha = payload.get("sha", "")
    version_tuple = parse_tag(tag)
    if not SHA_RE.fullmatch(sha):
        fail("sha must be a full lowercase commit SHA")

    version = ".".join(str(part) for part in version_tuple)
    exact = version
    sha_tag = f"sha-{sha}"
    existing = payload.get("existing_tags", {})
    for immutable in (exact, sha_tag):
        revision = existing.get(immutable)
        if revision is not None and revision != sha:
            fail(f"immutable tag {immutable} already refers to revision {revision}", 3)

    stable_releases = []
    for release in payload.get("releases", []):
        match = TAG_RE.fullmatch(release)
        if match:
            candidate = tuple(int(part) for part in match.groups())
            if candidate != version_tuple:
                stable_releases.append(candidate)
    previous_highest = max(stable_releases, default=None)
    globally_newest = previous_highest is None or version_tuple > previous_highest
    same_major = [item for item in stable_releases if item[0] == version_tuple[0]]
    major_newest = not same_major or version_tuple > max(same_major)

    tags = [exact, sha_tag, f"{version_tuple[0]}.{version_tuple[1]}"]
    if major_newest:
        tags.append(str(version_tuple[0]))
    if globally_newest:
        tags.append("latest")

    production = parse_version(payload.get("production_version", ""))
    deploy = production is None or version_tuple > production
    if not globally_newest:
        deploy = False

    print(
        json.dumps(
            {
                "version": version,
                "tags": tags,
                "deploy": deploy,
                "reuse_exact": exact in existing,
                "reuse_sha": sha_tag in existing,
            },
            separators=(",", ":"),
        )
    )


if __name__ == "__main__":
    main()

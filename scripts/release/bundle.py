#!/usr/bin/env python3
"""Build a deterministic, self-checksummed production deployment bundle."""

import argparse
import gzip
import hashlib
import io
import sys
import tarfile
from pathlib import Path, PurePosixPath


REQUIRED = {
    "docker-compose.yml": "compose.yaml",
    "docker-compose.aws.yml": "compose.aws.yaml",
    "scripts/deploy/deploy.sh": "scripts/deploy.sh",
    "infra/host/scripts/backup.sh": "scripts/backup.sh",
}


def add_bytes(archive: tarfile.TarFile, name: str, content: bytes, mode: int) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(content)
    info.mode = mode
    info.mtime = 0
    info.uid = info.gid = 0
    info.uname = info.gname = "root"
    archive.addfile(info, io.BytesIO(content))


def collect(root: Path):
    files = {}
    for source_name, archive_name in REQUIRED.items():
        source = root / source_name
        if not source.is_file() or source.is_symlink():
            raise ValueError(f"required regular file is missing: {source_name}")
        files[archive_name] = source

    host_root = root / "infra" / "host"
    for source in sorted(host_root.rglob("*")):
        if source.is_symlink():
            raise ValueError(f"bundle input must not be a symlink: {source.relative_to(root)}")
        if source.is_file():
            name = PurePosixPath(source.relative_to(host_root).as_posix()).as_posix()
            files.setdefault(name, source)
    return files


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    try:
        sources = collect(args.root.resolve())
    except ValueError as error:
        print(error, file=sys.stderr)
        raise SystemExit(1) from error

    payloads = {name: source.read_bytes() for name, source in sources.items()}
    sums = "".join(
        f"{hashlib.sha256(payloads[name]).hexdigest()}  {name}\n"
        for name in sorted(payloads)
    ).encode()
    payloads["SHA256SUMS"] = sums

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as archive:
                for name in sorted(payloads):
                    source = sources.get(name)
                    executable = source is not None and bool(source.stat().st_mode & 0o111)
                    add_bytes(archive, name, payloads[name], 0o755 if executable else 0o644)

    print(hashlib.sha256(args.output.read_bytes()).hexdigest())


if __name__ == "__main__":
    main()

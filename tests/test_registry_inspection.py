import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "release" / "inspect-tag.sh"


def run_case(tmp_path, mode):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    docker = fake_bin / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        "case \"$REGISTRY_MODE\" in\n"
        " valid) printf '%s\\n' '{\"annotations\":{\"org.opencontainers.image.revision\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\"}}' ;;\n"
        " absent) echo 'manifest unknown' >&2; exit 1 ;;\n"
        " absent_not_found) echo 'ERROR: docker.io/voxix/battlevive-bot:1.2.3: not found' >&2; exit 1 ;;\n"
        " unlabelled) printf '%s\\n' '{\"annotations\":{}}' ;;\n"
        " *) echo 'unauthorized or network failure' >&2; exit 1 ;;\n"
        "esac\n"
    )
    docker.chmod(0o755)
    return subprocess.run(
        [str(SCRIPT), "voxix/battlevive-bot:1.2.3"],
        env=os.environ | {"PATH": f"{fake_bin}:{os.environ['PATH']}", "REGISTRY_MODE": mode},
        text=True,
        capture_output=True,
    )


def test_returns_verified_revision(tmp_path):
    result = run_case(tmp_path, "valid")
    assert result.returncode == 0
    assert result.stdout.strip() == "a" * 40


def test_absent_tag_has_distinct_exit_status(tmp_path):
    result = run_case(tmp_path, "absent")
    assert result.returncode == 4


def test_not_found_tag_has_distinct_exit_status(tmp_path):
    """Buildx reports an absent Docker Hub tag as ``reference: not found``."""
    result = run_case(tmp_path, "absent_not_found")
    assert result.returncode == 4


def test_refuses_unlabelled_or_unverifiable_existing_tag(tmp_path):
    for mode in ("unlabelled", "error"):
        result = run_case(tmp_path, mode)
        assert result.returncode == 1
        assert "refusing" in result.stderr.lower()

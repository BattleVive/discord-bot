import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "release" / "inspect-tag.sh"
REVISION = "a" * 40


def run_http_case(tmp_path, status, body, *, token_body='{"token":"test-token"}'):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    docker = fake_bin / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        "echo 'docker must not be called for registry inspection' >&2\n"
        "exit 99\n"
    )
    docker.chmod(0o755)
    curl = fake_bin / "curl"
    curl.write_text(
        "#!/bin/sh\n"
        "args=\"$*\"\n"
        "output=\n"
        "while [ \"$#\" -gt 0 ]; do\n"
        "  case \"$1\" in\n"
        "    --output) output=$2; shift 2 ;;\n"
        "    *) shift ;;\n"
        "  esac\n"
        "done\n"
        "case \"$args\" in\n"
        "  *auth.docker.io/token*) printf '%s' \"$REGISTRY_TOKEN_BODY\" ;;\n"
        "  *registry-1.docker.io/v2/*)\n"
        "    printf '%s' \"$REGISTRY_BODY\" >\"$output\"\n"
        "    printf '%s' \"$REGISTRY_STATUS\"\n"
        "    ;;\n"
        "  *) echo 'unexpected curl request' >&2; exit 98 ;;\n"
        "esac\n"
    )
    curl.chmod(0o755)
    return subprocess.run(
        [str(SCRIPT), "voxix/battlevive-bot:1.2.3"],
        env=os.environ
        | {
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "REGISTRY_STATUS": status,
            "REGISTRY_BODY": body,
            "REGISTRY_TOKEN_BODY": token_body,
        },
        text=True,
        capture_output=True,
    )


def test_returns_verified_revision_from_registry_manifest(tmp_path):
    result = run_http_case(
        tmp_path,
        "200",
        f'{{"annotations":{{"org.opencontainers.image.revision":"{REVISION}"}}}}',
    )
    assert result.returncode == 0
    assert result.stdout.strip() == REVISION
    assert "docker must not be called" not in result.stderr


def test_registry_http_404_marks_a_tag_absent_without_calling_docker(tmp_path):
    result = run_http_case(tmp_path, "404", '{"errors":[{"code":"MANIFEST_UNKNOWN"}]}')
    assert result.returncode == 4
    assert "docker must not be called" not in result.stderr


def test_refuses_existing_manifest_without_a_verifiable_revision(tmp_path):
    result = run_http_case(tmp_path, "200", '{"annotations":{}}')
    assert result.returncode == 1
    assert "verifiable OCI revision" in result.stderr


def test_refuses_non_absent_registry_statuses(tmp_path):
    for status in ("401", "403", "429", "500"):
        result = run_http_case(tmp_path, status, '{"errors":[]}')
        assert result.returncode == 1
        assert f"HTTP {status}" in result.stderr


def test_refuses_malformed_pull_token(tmp_path):
    result = run_http_case(tmp_path, "404", '{"errors":[]}', token_body='{}')
    assert result.returncode == 1
    assert "Unable to obtain a Docker Hub pull token" in result.stderr

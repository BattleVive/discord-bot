import hashlib
import json
import os
import subprocess
import tarfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "scripts" / "deploy" / "deploy.sh"


def executable(path: Path, body: str):
    path.write_text("#!/bin/sh\nset -eu\n" + body)
    path.chmod(0o755)


def make_bundle(tmp_path: Path):
    content = tmp_path / "content"
    (content / "scripts").mkdir(parents=True)
    (content / "compose.yaml").write_text("services: {}\n")
    (content / "compose.aws.yaml").write_text("services: {}\n")
    executable(
        content / "scripts" / "backup.sh",
        'test "${FAIL_STAGE:-}" != backup\nprintf "backup verified\\n"\n',
    )
    entries = []
    for relative in ("compose.yaml", "compose.aws.yaml", "scripts/backup.sh"):
        digest = hashlib.sha256((content / relative).read_bytes()).hexdigest()
        entries.append(f"{digest}  {relative}\n")
    (content / "SHA256SUMS").write_text("".join(entries))
    archive = tmp_path / "bundle.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        for item in content.rglob("*"):
            output.add(item, arcname=item.relative_to(content))
    return archive, hashlib.sha256(archive.read_bytes()).hexdigest()


def prepare(tmp_path: Path, fail_stage=""):
    archive, checksum = make_bundle(tmp_path)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "commands.log"
    executable(
        fake_bin / "aws",
        '''printf 'aws %s\\n' "$*" >> "$COMMAND_LOG"
if [ "$1 $2" = "s3 cp" ]; then
  test "${FAIL_STAGE:-}" != download
  /bin/cp "$TEST_ARCHIVE" "$4"
fi
''',
    )
    executable(
        fake_bin / "docker",
        '''printf 'docker %s\\n' "$*" >> "$COMMAND_LOG"
case "$*" in
  *"run --rm migration"*) test "${FAIL_STAGE:-}" != migration ;;
  *"up -d bot"*) test "${FAIL_STAGE:-}" != startup ;;
  *"inspect --format {{.Config.Image}}"*) printf '%s\\n' "${INSPECT_IMAGE:-voxix/battlevive-bot@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa}" ;;
  *"inspect --format {{.State.Health.Status}}"*)
    if [ "${FAIL_STAGE:-}" = health ]; then printf 'unhealthy\\n'; else printf 'healthy\\n'; fi ;;
esac
''',
    )
    deploy_root = tmp_path / "deploy"
    previous = deploy_root / "releases" / "1.0.0"
    previous.mkdir(parents=True)
    (previous / "compose.yaml").write_text("services: {}\n")
    (previous / "compose.aws.yaml").write_text("services: {}\n")
    (previous / ".image").write_text(
        "voxix/battlevive-bot@sha256:" + "b" * 64 + "\n"
    )
    (deploy_root / "current").symlink_to(previous)
    env = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "COMMAND_LOG": str(log),
        "TEST_ARCHIVE": str(archive),
        "FAIL_STAGE": fail_stage,
        "BATTLEVIVE_DEPLOY_ROOT": str(deploy_root),
        "OPERATIONS_BUCKET": "test-operations",
        "AWS_REGION": "eu-north-1",
        "HEALTH_TIMEOUT_SECONDS": "1",
        "HEALTH_POLL_SECONDS": "0",
    }
    args = [
        str(DEPLOY),
        "--version", "1.1.0",
        "--image-digest", "sha256:" + "a" * 64,
        "--bundle-key", "releases/1.1.0.tar.gz",
        "--bundle-checksum", checksum,
        "--target-selector", "Project=battlevive-bot,Environment=production",
    ]
    return args, env, deploy_root, log


def test_success_promotes_verified_digest_then_publishes_state(tmp_path):
    args, env, deploy_root, log = prepare(tmp_path)
    result = subprocess.run(args, env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert (deploy_root / "current").resolve().name == "1.1.0-aaaaaaaaaaaa"
    state = json.loads(((deploy_root / "current").resolve() / ".deployment.json").read_text())
    assert state["version"] == "1.1.0"
    assert state["image_digest"] == "sha256:" + "a" * 64
    assert state["bundle_key"] == "releases/1.1.0.tar.gz"
    commands = log.read_text()
    assert "run --rm migration" in commands
    assert "inspect --format {{.State.Health.Status}} battlevive-bot" in commands
    assert "/battlevive/production/deployment/version" in commands
    assert "/battlevive/production/deployment/image-digest" in commands
    assert commands.index("run --rm migration") < commands.index("up -d bot")
    assert commands.index("inspect --format {{.State.Health.Status}}") < commands.index(
        "/battlevive/production/deployment/version"
    )


@pytest.mark.parametrize("stage", ["download", "backup", "migration", "startup", "health"])
def test_each_failed_stage_keeps_or_restores_previous_release(tmp_path, stage):
    args, env, deploy_root, log = prepare(tmp_path, stage)
    result = subprocess.run(args, env=env, text=True, capture_output=True)
    assert result.returncode != 0
    assert (deploy_root / "current").resolve().name == "1.0.0"
    commands = log.read_text() if log.exists() else ""
    assert "/battlevive/production/deployment/version" not in commands
    assert "DeploymentFailure" in commands
    if stage in {"migration", "startup", "health"}:
        assert "voxix/battlevive-bot@sha256:" + "b" * 64 in commands


def test_wrong_bundle_checksum_stops_before_backup_or_docker(tmp_path):
    args, env, deploy_root, log = prepare(tmp_path)
    args[args.index("--bundle-checksum") + 1] = "0" * 64
    result = subprocess.run(args, env=env, text=True, capture_output=True)
    assert result.returncode != 0
    assert (deploy_root / "current").resolve().name == "1.0.0"
    assert "docker " not in log.read_text()


def test_rejects_floating_or_malformed_image_reference(tmp_path):
    args, env, _, _ = prepare(tmp_path)
    args[args.index("--image-digest") + 1] = "latest"
    result = subprocess.run(args, env=env, text=True, capture_output=True)
    assert result.returncode == 2
    assert "sha256 digest" in result.stderr

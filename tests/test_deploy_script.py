import hashlib
import json
import os
import re
import subprocess
import tarfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "scripts" / "deploy" / "deploy.sh"
DEPLOYMENT_STATE = ROOT / "infra" / "modules" / "production" / "storage.tf"

TERRAFORM_BOOTSTRAP_STATE = {
    "version": "0.0.0",
    "image_digest": "",
    "bundle_key": "",
    "bundle_checksum": "",
}


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
        '''test "${FAIL_STAGE:-}" != backup
test "${BATTLEVIVE_OPERATIONS_LOCK_HELD:-}" = 1
printf "backup verified\\n"
''',
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
if [ "$1 $2" = "ssm get-parameter" ]; then
  if [ -n "${CURRENT_STATE:-}" ]; then printf '%s\\n' "$CURRENT_STATE"; else echo ParameterNotFound >&2; exit 254; fi
fi
if [ "$1 $2" = "ssm put-parameter" ]; then
  test "${FAIL_STAGE:-}" != state_write
  test "${FAIL_STAGE:-}" != state_write_rollback_health
fi
if [ "$1 $2" = "cloudwatch put-metric-data" ] && [ "${FAIL_STAGE:-}" = telemetry ]; then
  exit 1
fi
''',
    )
    executable(
        fake_bin / "docker",
        '''printf 'docker %s\\n' "$*" >> "$COMMAND_LOG"
case "$*" in
  *"run --rm migration"*)
    if flock -n "${OPERATIONS_LOCK_PATH:?}" true; then echo 'operations lock was not held' >&2; exit 8; fi
    if [ "${FAIL_STAGE:-}" = migration ] && printf '%s' "${BATTLEVIVE_IMAGE:-}" | grep -q 'aaaa'; then exit 1; fi ;;
  *"up -d bot"*)
    if flock -n "${OPERATIONS_LOCK_PATH:?}" true; then echo 'operations lock was not held' >&2; exit 8; fi
    if [ "${FAIL_STAGE:-}" = startup ] && printf '%s' "${BATTLEVIVE_IMAGE:-}" | grep -q 'aaaa'; then exit 1; fi
    printf '%s\\n' "$BATTLEVIVE_IMAGE" > "$RUNNING_IMAGE_FILE" ;;
  *"inspect --format {{.Config.Image}}"*) cat "$RUNNING_IMAGE_FILE" ;;
  *"inspect --format {{.State.Health.Status}}"*)
    if { [ "${FAIL_STAGE:-}" = health ] && grep -q 'aaaa' "$RUNNING_IMAGE_FILE"; } ||
       { [ "${FAIL_STAGE:-}" = state_write_rollback_health ] && grep -q 'bbbb' "$RUNNING_IMAGE_FILE"; };
    then printf 'unhealthy\\n'; else printf 'healthy\\n'; fi ;;
esac
''',
    )
    deploy_root = tmp_path / "opt" / "battlevive"
    previous = deploy_root / "releases" / "1.0.0"
    previous.mkdir(parents=True)
    (previous / "compose.yaml").write_text("services: {}\n")
    (previous / "compose.aws.yaml").write_text("services: {}\n")
    (previous / ".image").write_text(
        "voxix/battlevive-bot@sha256:" + "b" * 64 + "\n"
    )
    previous_state = {
        "version": "1.0.0",
        "image_digest": "sha256:" + "b" * 64,
        "bundle_key": "releases/1.0.0.tar.gz",
        "bundle_checksum": "c" * 64,
    }
    (previous / ".deployment.json").write_text(json.dumps(previous_state))
    (deploy_root / "current").symlink_to(previous)
    host_env = tmp_path / "host.env"
    host_env.write_text(
        "AWS_REGION=eu-north-1\n"
        "OPERATIONS_BUCKET=test-operations\n"
        "BATTLEVIVE_BOT_DATA_PATH=/var/lib/battlevive/bot\n"
        "BATTLEVIVE_POSTGRES_DATA_PATH=/var/lib/battlevive/postgresql\n"
        "BATTLEVIVE_LOG_GROUP=/battlevive/production/application\n"
    )
    host_env.chmod(0o600)
    running_image = tmp_path / "running-image"
    running_image.write_text("voxix/battlevive-bot@sha256:" + "b" * 64 + "\n")
    operations_lock = tmp_path / "operations.lock"
    env = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "COMMAND_LOG": str(log),
        "TEST_ARCHIVE": str(archive),
        "FAIL_STAGE": fail_stage,
        "BATTLEVIVE_ROOT": str(deploy_root),
        "BATTLEVIVE_HOST_ENV": str(host_env),
        "OPERATIONS_LOCK_PATH": str(operations_lock),
        "RUNNING_IMAGE_FILE": str(running_image),
        "CURRENT_STATE": json.dumps(previous_state, separators=(",", ":")),
        "ALLOW_NON_ROOT_FOR_TESTS": "1",
        "HEALTH_TIMEOUT_SECONDS": "1",
        "ROLLBACK_TIMEOUT_SECONDS": "1",
        "HEALTH_POLL_SECONDS": "0",
    }
    args = [
        str(DEPLOY),
        "--environment", "production",
        "--version", "1.1.0",
        "--image-digest", "sha256:" + "a" * 64,
        "--bundle-key", "releases/1.1.0.tar.gz",
        "--bundle-checksum", checksum,
        "--target-selector", "Project=battlevive-bot,Environment=production",
        "--operations-bucket", "test-operations",
        "--aws-region", "eu-north-1",
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
    assert "/battlevive/production/deployment/state" in commands
    assert commands.count("ssm put-parameter") == 1
    assert "--env-file" in commands
    assert commands.index("run --rm migration") < commands.index("up -d bot")
    assert commands.index("inspect --format {{.State.Health.Status}}") < commands.index(
        "ssm put-parameter"
    )


def test_terraform_bootstrap_state_allows_first_deployment(tmp_path):
    args, env, deploy_root, log = prepare(tmp_path)
    (deploy_root / "current").unlink()
    env["CURRENT_STATE"] = json.dumps(TERRAFORM_BOOTSTRAP_STATE, separators=(",", ":"))

    result = subprocess.run(args, env=env, text=True, capture_output=True)

    assert result.returncode == 0, result.stderr
    assert (deploy_root / "current").resolve().name == "1.1.0-aaaaaaaaaaaa"
    assert "s3 cp" in log.read_text()


def test_terraform_initial_state_matches_the_host_bootstrap_contract():
    configuration = DEPLOYMENT_STATE.read_text()

    for key, value in TERRAFORM_BOOTSTRAP_STATE.items():
        assert re.search(rf'\b{key}\s*=\s*"{re.escape(value)}"', configuration)


def test_noncanonical_bootstrap_state_remains_rejected(tmp_path):
    args, env, _, log = prepare(tmp_path)
    env["CURRENT_STATE"] = json.dumps(
        {
            "version": "0.0.0",
            "image_digest": "sha256:" + "0" * 64,
            "bundle_key": "",
            "bundle_checksum": "",
        },
        separators=(",", ":"),
    )

    result = subprocess.run(args, env=env, text=True, capture_output=True)

    assert result.returncode != 0
    assert "deployment state is malformed" in result.stderr
    assert "s3 cp" not in log.read_text()


@pytest.mark.parametrize("stage", ["download", "backup", "migration", "startup", "health", "state_write"])
def test_each_failed_stage_keeps_or_restores_previous_release(tmp_path, stage):
    args, env, deploy_root, log = prepare(tmp_path, stage)
    result = subprocess.run(args, env=env, text=True, capture_output=True)
    assert result.returncode != 0
    assert (deploy_root / "current").resolve().name == "1.0.0"
    commands = log.read_text() if log.exists() else ""
    assert "ssm put-parameter" not in commands or stage == "state_write"
    assert "DeploymentFailure" in commands
    if stage in {"migration", "startup", "health", "state_write"}:
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


def test_host_rejects_downgrade_before_download(tmp_path):
    args, env, deploy_root, log = prepare(tmp_path)
    args[args.index("--version") + 1] = "0.9.0"
    result = subprocess.run(args, env=env, text=True, capture_output=True)
    assert result.returncode != 0
    assert "downgrade" in result.stderr
    assert "s3 cp" not in log.read_text()
    assert (deploy_root / "current").resolve().name == "1.0.0"


def test_host_rejects_downgrade_from_unbounded_semver_component(tmp_path):
    args, env, _, log = prepare(tmp_path)
    state = json.loads(env["CURRENT_STATE"])
    state["version"] = "999999999999999999999.0.0"
    env["CURRENT_STATE"] = json.dumps(state, separators=(",", ":"))
    result = subprocess.run(args, env=env, text=True, capture_output=True)
    assert result.returncode != 0
    assert "downgrade" in result.stderr
    assert "s3 cp" not in log.read_text()


def test_rejects_noncanonical_host_data_paths(tmp_path):
    args, env, _, log = prepare(tmp_path)
    Path(env["BATTLEVIVE_HOST_ENV"]).write_text(
        "AWS_REGION=eu-north-1\nOPERATIONS_BUCKET=test-operations\n"
        "BATTLEVIVE_BOT_DATA_PATH=./data\n"
        "BATTLEVIVE_POSTGRES_DATA_PATH=/var/lib/battlevive/postgresql\n"
        "BATTLEVIVE_LOG_GROUP=/battlevive/production/application\n"
    )
    result = subprocess.run(args, env=env, text=True, capture_output=True)
    assert result.returncode != 0
    assert "canonical host environment" in result.stderr
    assert not log.exists()


def test_success_metric_failure_does_not_rollback_success(tmp_path):
    args, env, deploy_root, log = prepare(tmp_path, "telemetry")
    result = subprocess.run(args, env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert (deploy_root / "current").resolve().name == "1.1.0-aaaaaaaaaaaa"
    assert "DeploymentSuccess" in log.read_text()
    assert "telemetry" in result.stderr


def test_rollback_failure_is_reported_as_critical(tmp_path):
    args, env, _, _ = prepare(tmp_path, "state_write_rollback_health")
    result = subprocess.run(args, env=env, text=True, capture_output=True)
    assert result.returncode != 0
    assert "CRITICAL: rollback failed health or digest verification" in result.stderr

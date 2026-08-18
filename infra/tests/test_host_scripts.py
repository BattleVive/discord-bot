from __future__ import annotations

import fcntl
import json
import os
import stat
import subprocess
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HOST = ROOT / "infra" / "host" / "bin"
TOOLS = ROOT / "infra" / "tools"


def executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def run_script(path: Path, *args: str, env: dict[str, str] | None = None, stdin: str = ""):
    merged = os.environ.copy()
    if env:
        merged.update(env)
    return subprocess.run(
        [str(path), *args],
        input=stdin,
        text=True,
        capture_output=True,
        env=merged,
        check=False,
    )


def test_compose_launcher_uses_standalone_binary_when_docker_plugin_is_unavailable(
    tmp_path: Path,
) -> None:
    calls = tmp_path / "calls.log"
    fake_docker = tmp_path / "docker"
    executable(
        fake_docker,
        """#!/bin/sh
if [ "$1 $2" = "compose version" ]; then exit 1; fi
printf 'docker %s\\n' "$*" >>"$CALLS"
""",
    )
    fake_compose = tmp_path / "docker-compose"
    executable(fake_compose, "#!/bin/sh\nprintf 'standalone %s\\n' \"$*\" >>\"$CALLS\"\n")

    result = run_script(
        HOST / "compose",
        "--env-file",
        "/dev/null",
        "-f",
        "/tmp/compose.yaml",
        "config",
        "--quiet",
        env={
            "DOCKER_CLI": str(fake_docker),
            "DOCKER_COMPOSE_CLI": str(fake_compose),
            "CALLS": str(calls),
        },
    )

    assert result.returncode == 0, result.stderr
    assert calls.read_text() == (
        "standalone --env-file /dev/null -f /tmp/compose.yaml config --quiet\n"
    )


def test_compose_launcher_prefers_docker_compose_plugin(tmp_path: Path) -> None:
    calls = tmp_path / "calls.log"
    fake_docker = tmp_path / "docker"
    executable(
        fake_docker,
        """#!/bin/sh
if [ "$1 $2" = "compose version" ]; then exit 0; fi
printf 'docker %s\\n' "$*" >>"$CALLS"
""",
    )
    fake_compose = tmp_path / "docker-compose"
    executable(fake_compose, "#!/bin/sh\nprintf 'standalone %s\\n' \"$*\" >>\"$CALLS\"\n")

    result = run_script(
        HOST / "compose",
        "-f",
        "/tmp/compose.yaml",
        "config",
        "--quiet",
        env={
            "DOCKER_CLI": str(fake_docker),
            "DOCKER_COMPOSE_CLI": str(fake_compose),
            "CALLS": str(calls),
        },
    )

    assert result.returncode == 0, result.stderr
    assert calls.read_text() == "docker compose -f /tmp/compose.yaml config --quiet\n"


def test_ssm_session_resolves_exactly_one_tagged_instance(tmp_path: Path) -> None:
    fake_aws = tmp_path / "aws"
    executable(
        fake_aws,
        """#!/bin/sh
case "$*" in
  *describe-instances*) printf 'i-0123456789abcdef0\\n' ;;
  *start-session*) printf '%s\\n' "$*" ;;
esac
""",
    )

    result = run_script(
        TOOLS / "ssm-session.sh",
        env={"AWS_CLI": str(fake_aws), "AWS_PROFILE_NAME": "test-profile"},
    )

    assert result.returncode == 0, result.stderr
    assert "--target i-0123456789abcdef0" in result.stdout
    assert "--region eu-north-1" in result.stdout
    assert "--profile test-profile" in result.stdout


def test_ssm_session_refuses_ambiguous_inventory(tmp_path: Path) -> None:
    fake_aws = tmp_path / "aws"
    executable(
        fake_aws,
        """#!/bin/sh
printf 'i-11111111111111111\\ti-22222222222222222\\n'
""",
    )

    result = run_script(TOOLS / "ssm-session.sh", env={"AWS_CLI": str(fake_aws)})

    assert result.returncode != 0
    assert "exactly one" in result.stderr


def test_secret_renderer_writes_runtime_group_files_without_printing_values(tmp_path: Path) -> None:
    fake_aws = tmp_path / "aws"
    executable(
        fake_aws,
        """#!/bin/sh
case "$*" in
  */database-url*) printf 'postgresql://secret-db\\n' ;;
  */discord-token*) printf 'secret-discord\\n' ;;
  */supabase-api-key*) printf 'secret-supabase\\n' ;;
  */bootstrap-jwt*) printf 'secret-jwt\\n' ;;
  */bootstrap-refresh-token*) printf 'secret-refresh\\n' ;;
  */postgres-password*) printf 'secret-postgres\\n' ;;
  *) exit 9 ;;
esac
""",
    )
    destination = tmp_path / "run" / "battlevive"

    result = run_script(
        HOST / "render-secrets.sh",
        env={
            "AWS_CLI": str(fake_aws),
            "BATTLEVIVE_SECRET_DIR": str(destination),
            "ALLOW_NON_ROOT_FOR_TESTS": "1",
            "RUNTIME_GID": str(os.getgid()),
        },
    )

    assert result.returncode == 0, result.stderr
    expected = {
        "database-url": "postgresql://secret-db",
        "discord-token": "secret-discord",
        "supabase-api-key": "secret-supabase",
        "bootstrap-jwt": "secret-jwt",
        "bootstrap-refresh-token": "secret-refresh",
        "postgres-password": "secret-postgres",
    }
    for filename, value in expected.items():
        path = destination / filename
        assert path.read_text() == value
        assert stat.S_IMODE(path.stat().st_mode) == 0o640
        assert path.stat().st_gid == os.getgid()
    assert stat.S_IMODE(destination.stat().st_mode) == 0o750
    combined = result.stdout + result.stderr
    assert all(value not in combined for value in expected.values())


def test_secret_renderer_preserves_previous_set_when_download_fails(tmp_path: Path) -> None:
    fake_aws = tmp_path / "aws"
    executable(
        fake_aws,
        """#!/bin/sh
case "$*" in
  */database-url*) printf 'new-value\\n' ;;
  *) exit 8 ;;
esac
""",
    )
    destination = tmp_path / "run" / "battlevive"
    destination.mkdir(parents=True)
    old = destination / "database-url"
    old.write_text("old-value")

    result = run_script(
        HOST / "render-secrets.sh",
        env={
            "AWS_CLI": str(fake_aws),
            "BATTLEVIVE_SECRET_DIR": str(destination),
            "ALLOW_NON_ROOT_FOR_TESTS": "1",
        },
    )

    assert result.returncode != 0
    assert old.read_text() == "old-value"
    assert not any(destination.glob("*.tmp.*"))


def test_secret_renderer_refuses_empty_parameter(tmp_path: Path) -> None:
    fake_aws = tmp_path / "aws"
    executable(fake_aws, "#!/bin/sh\nprintf '\\n'\n")
    destination = tmp_path / "run" / "battlevive"

    result = run_script(
        HOST / "render-secrets.sh",
        env={
            "AWS_CLI": str(fake_aws),
            "BATTLEVIVE_SECRET_DIR": str(destination),
            "ALLOW_NON_ROOT_FOR_TESTS": "1",
        },
    )

    assert result.returncode != 0
    assert not (destination / "database-url").exists()


def test_inventory_output_is_sanitized_and_contains_no_user_data(tmp_path: Path) -> None:
    fake_aws = tmp_path / "aws"
    executable(
        fake_aws,
        """#!/bin/sh
cat <<'JSON'
{"Reservations":[{"Instances":[{"InstanceId":"i-abc","Architecture":"arm64","ImageId":"ami-abc","Placement":{"AvailabilityZone":"eu-north-1a"},"VpcId":"vpc-abc","SubnetId":"subnet-abc","PublicIpAddress":"198.51.100.10","IamInstanceProfile":{"Arn":"arn:aws:iam::123456789012:instance-profile/example"},"SecurityGroups":[{"GroupId":"sg-abc"}],"BlockDeviceMappings":[]}]}]}
JSON
""",
    )

    result = run_script(
        TOOLS / "inventory.sh",
        env={"AWS_CLI": str(fake_aws), "ALLOW_NON_ROOT_FOR_TESTS": "1"},
    )

    assert result.returncode == 0, result.stderr
    inventory = json.loads(result.stdout)
    assert inventory["instance_id"] == "i-abc"
    assert inventory["security_group_ids"] == ["sg-abc"]
    assert "UserData" not in result.stdout
    assert "Tags" not in result.stdout


def test_predeploy_backup_validates_and_uploads_archive_sidecars(tmp_path: Path) -> None:
    fake_docker = tmp_path / "docker"
    executable(
        fake_docker,
        """#!/bin/sh
case "$*" in
  *'pg_dump'*) printf 'PGDMP-test-archive' ;;
  *'pg_restore --list'*) cat >/dev/null ;;
  *'to_regclass('*) printf 't\\n' ;;
  *'MAX(version)'*) printf '9\\n' ;;
  *'count(*) FROM schema_migrations'*) printf '9\\n' ;;
  *'json_build_object'*) printf '{"schema_migrations":9,"guild_config":4}\\n' ;;
  *) exit 12 ;;
esac
""",
    )
    aws_log = tmp_path / "aws.log"
    fake_aws = tmp_path / "aws"
    executable(
        fake_aws,
        """#!/bin/sh
printf '%s\\n' "$*" >>"$AWS_LOG"
case "$*" in
  *'/deployment/state'*) printf '{"version":"1.2.3"}\\n' ;;
esac
""",
    )

    result = run_script(
        ROOT / "infra" / "host" / "scripts" / "backup.sh",
        "--type",
        "pre-deploy",
        "--verify",
        env={
            "AWS_CLI": str(fake_aws),
            "AWS_LOG": str(aws_log),
            "BATTLEVIVE_COMPOSE_CLI": str(fake_docker),
            "OPERATIONS_BUCKET": "test-operations-bucket",
            "BACKUP_TMP_ROOT": str(tmp_path),
            "ALLOW_NON_ROOT_FOR_TESTS": "1",
        },
    )

    assert result.returncode == 0, result.stderr
    calls = aws_log.read_text()
    assert "s3 cp" in calls
    assert "backups/predeploy/" in calls
    assert calls.count("s3 cp") == 3
    assert "--metric-name BackupSuccess --value 1" in calls


def test_predeploy_backup_supports_existing_database_without_migration_ledger(
    tmp_path: Path,
) -> None:
    fake_compose = tmp_path / "compose"
    executable(
        fake_compose,
        """#!/bin/sh
case "$*" in
  *'pg_dump'*) printf 'PGDMP-existing-schema' ;;
  *'pg_restore --list'*) cat >/dev/null ;;
  *'to_regclass('*) printf 'f\\n' ;;
  *'MAX(version)'*|*'count(*) FROM schema_migrations'*)
    echo 'schema_migrations is unavailable before first migration' >&2
    exit 42 ;;
  *'json_build_object'*) printf '{"schema_migrations":0,"guild_config":4}\\n' ;;
  *) exit 12 ;;
esac
""",
    )
    manifest = tmp_path / "predeploy.manifest.json"
    aws_log = tmp_path / "aws.log"
    fake_aws = tmp_path / "aws"
    executable(
        fake_aws,
        """#!/bin/sh
printf '%s\\n' "$*" >>"$AWS_LOG"
case "$*" in
  *'/deployment/state'*) printf '{"version":"0.0.0"}\\n' ;;
  *'s3 cp'*'.manifest.json'*) cp "$3" "$MANIFEST" ;;
esac
""",
    )

    result = run_script(
        ROOT / "infra" / "host" / "scripts" / "backup.sh",
        "--type",
        "pre-deploy",
        "--verify",
        env={
            "AWS_CLI": str(fake_aws),
            "AWS_LOG": str(aws_log),
            "BATTLEVIVE_COMPOSE_CLI": str(fake_compose),
            "OPERATIONS_BUCKET": "test-operations-bucket",
            "BACKUP_TMP_ROOT": str(tmp_path),
            "MANIFEST": str(manifest),
            "ALLOW_NON_ROOT_FOR_TESTS": "1",
        },
    )

    assert result.returncode == 0, result.stderr
    recorded = json.loads(manifest.read_text())
    assert recorded["schema_version"] == 0
    assert recorded["row_counts"] == {"schema_migrations": 0, "guild_config": 4}
    assert "backups/predeploy/" in aws_log.read_text()


def test_backup_failure_emits_failure_metric_and_returns_nonzero(tmp_path: Path) -> None:
    fake_docker = tmp_path / "docker"
    executable(fake_docker, "#!/bin/sh\nexit 42\n")
    aws_log = tmp_path / "aws.log"
    fake_aws = tmp_path / "aws"
    executable(fake_aws, "#!/bin/sh\nprintf '%s\\n' \"$*\" >>\"$AWS_LOG\"\n")

    result = run_script(
        ROOT / "infra" / "host" / "scripts" / "backup.sh",
        "predeploy",
        env={
            "AWS_CLI": str(fake_aws),
            "AWS_LOG": str(aws_log),
            "BATTLEVIVE_COMPOSE_CLI": str(fake_docker),
            "OPERATIONS_BUCKET": "test-operations-bucket",
            "BACKUP_TMP_ROOT": str(tmp_path),
            "ALLOW_NON_ROOT_FOR_TESTS": "1",
        },
    )

    assert result.returncode != 0
    assert "--metric-name BackupSuccess --value 0" in aws_log.read_text()


def test_host_installer_creates_canonical_runtime_contract(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    for directory in ("bin", "scripts", "systemd", "rsyslog"):
        (bundle / directory).mkdir(parents=True, exist_ok=True)
    for relative in (
        "bin/render-secrets.sh",
        "bin/compose",
        "scripts/deploy.sh",
        "scripts/backup.sh",
        "scripts/restore-verify.sh",
        "scripts/publish-health.sh",
        "scripts/publish-operations-freshness.sh",
    ):
        executable(bundle / relative, "#!/bin/sh\nexit 0\n")
    (bundle / "systemd" / "test.service").write_text("[Service]\nType=oneshot\n")
    (bundle / "systemd" / "test.timer").write_text("[Timer]\nOnCalendar=daily\n")
    (bundle / "rsyslog" / "30-battlevive-messages.conf").write_text("*.info /var/log/messages\n")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for command in ("systemctl", "dnf"):
        executable(fake_bin / command, "#!/bin/sh\nexit 0\n")
    fake_aws = fake_bin / "aws"
    executable(fake_aws, "#!/bin/sh\nprintf 'operations-test-bucket\\n'\n")
    install_root = tmp_path / "root"

    result = run_script(
        ROOT / "infra" / "host" / "install.sh",
        env={
            "ALLOW_NON_ROOT_FOR_TESTS": "1",
            "BATTLEVIVE_BUNDLE_ROOT": str(bundle),
            "INSTALL_ROOT": str(install_root),
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "RUNTIME_UID": str(os.getuid()),
            "RUNTIME_GID": str(os.getgid()),
            "POSTGRES_UID": str(os.getuid()),
            "POSTGRES_GID": str(os.getgid()),
        },
    )

    assert result.returncode == 0, result.stderr
    deploy = install_root / "usr/local/libexec/battlevive/deploy"
    assert deploy.exists() and os.access(deploy, os.X_OK)
    compose = install_root / "usr/local/libexec/battlevive/compose"
    assert compose.exists() and os.access(compose, os.X_OK)
    host_env = install_root / "run/battlevive/host.env"
    assert stat.S_IMODE(host_env.stat().st_mode) == 0o600
    assert host_env.read_text().splitlines() == [
        "AWS_REGION=eu-north-1",
        "OPERATIONS_BUCKET=operations-test-bucket",
        "BATTLEVIVE_DEPLOY_ROOT=/opt/battlevive",
        "BATTLEVIVE_BOT_DATA_PATH=/var/lib/battlevive/bot",
        "BATTLEVIVE_POSTGRES_DATA_PATH=/var/lib/battlevive/postgresql",
        "BATTLEVIVE_LOG_GROUP=/battlevive/production/application",
        "POSTGRES_USER=battlevive",
        "POSTGRES_DB=battlevive",
    ]
    assert (install_root / "var/lib/battlevive/bot").stat().st_uid == os.getuid()
    assert (install_root / "var/lib/battlevive/postgresql").stat().st_uid == os.getuid()
    assert (install_root / "etc/rsyslog.d/30-battlevive-messages.conf").exists()


def test_host_installer_starts_runtime_secret_renderer_on_bootstrap() -> None:
    installer = (ROOT / "infra" / "host" / "install.sh").read_text(encoding="utf-8")

    assert "systemctl enable --now battlevive-secrets.service" in installer


def test_predeploy_backup_refuses_concurrent_operations_lock(tmp_path: Path) -> None:
    lock_path = tmp_path / "operations.lock"
    lock_path.touch()
    with lock_path.open("w") as held_lock:
        fcntl.flock(held_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = run_script(
            ROOT / "infra" / "host" / "scripts" / "backup.sh",
            "--type",
            "pre-deploy",
            "--verify",
            env={
                "BATTLEVIVE_OPERATIONS_LOCK": str(lock_path),
                "ALLOW_NON_ROOT_FOR_TESTS": "1",
            },
        )

    assert result.returncode != 0
    assert "operation is active" in result.stderr


def resize_gate_aws(tmp_path: Path, *, sparse: bool) -> tuple[Path, Path]:
    log = tmp_path / "metrics.log"
    fake = tmp_path / "aws-resize"
    executable(
        fake,
        f"""#!/usr/bin/env python3
import datetime
import json
import os
import sys

args = sys.argv[1:]
if "describe-instances" in args:
    print("i-test")
    raise SystemExit
metric = args[args.index("--metric-name") + 1]
period = int(args[args.index("--period") + 1])
start = datetime.datetime.fromisoformat(args[args.index("--start-time") + 1].replace("Z", "+00:00"))
end = datetime.datetime.fromisoformat(args[args.index("--end-time") + 1].replace("Z", "+00:00"))
with open(os.environ["METRIC_LOG"], "a") as stream:
    stream.write(metric + "\\n")
values = {{
    "mem_used": 200_000_000, "swap_used": 0, "disk_used_percent": 50,
    "disk_inodes_used": 40, "disk_inodes_total": 100, "CPUCreditBalance": 100,
    "CPUSurplusCreditsCharged": 0, "StatusCheckFailed": 0, "BotHealthy": 1,
    "OOMEvents": 0, "HostTelemetryHealthy": 1,
}}
count = 1 if {str(sparse)} else int((end - start).total_seconds() // period)
points = []
for index in range(count):
    timestamp = start + datetime.timedelta(seconds=(index + 1) * period)
    value = values[metric]
    points.append({{"Timestamp": timestamp.isoformat().replace("+00:00", "Z"), "Maximum": value, "Minimum": value, "Average": value, "Sum": value}})
print(json.dumps({{"Datapoints": points}}))
""",
    )
    return fake, log


def test_resize_gate_fails_closed_on_sparse_168_hour_metrics(tmp_path: Path) -> None:
    fake_aws, metric_log = resize_gate_aws(tmp_path, sparse=True)
    result = run_script(
        TOOLS / "resize-gate.sh",
        env={"AWS_CLI": str(fake_aws), "METRIC_LOG": str(metric_log)},
    )

    assert result.returncode != 0
    assert "continuous coverage" in result.stderr


def test_resize_gate_requires_all_capacity_and_failure_metrics(tmp_path: Path) -> None:
    fake_aws, metric_log = resize_gate_aws(tmp_path, sparse=False)
    result = run_script(
        TOOLS / "resize-gate.sh",
        env={"AWS_CLI": str(fake_aws), "METRIC_LOG": str(metric_log)},
    )

    assert result.returncode == 0, result.stderr
    assert set(metric_log.read_text().splitlines()) == {
        "mem_used",
        "swap_used",
        "disk_used_percent",
        "disk_inodes_used",
        "disk_inodes_total",
        "CPUCreditBalance",
        "CPUSurplusCreditsCharged",
        "StatusCheckFailed",
        "BotHealthy",
        "OOMEvents",
        "HostTelemetryHealthy",
    }


def test_health_publisher_emits_zero_oom_and_host_telemetry_heartbeat(tmp_path: Path) -> None:
    fake_docker = tmp_path / "docker"
    timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    executable(
        fake_docker,
        f"#!/bin/sh\nprintf '%s\\n' '{json.dumps({'status': 'healthy', 'timestamp': timestamp})}'\n",
    )
    fake_systemctl = tmp_path / "systemctl"
    executable(fake_systemctl, "#!/bin/sh\nexit 0\n")
    aws_log = tmp_path / "aws.log"
    fake_aws = tmp_path / "aws"
    executable(fake_aws, "#!/bin/sh\nprintf '%s\\n' \"$*\" >>\"$AWS_LOG\"\n")
    messages = tmp_path / "messages"
    messages.write_text("system ready\n")

    result = run_script(
        ROOT / "infra" / "host" / "scripts" / "publish-health.sh",
        env={
            "AWS_CLI": str(fake_aws),
            "AWS_LOG": str(aws_log),
            "BATTLEVIVE_COMPOSE_CLI": str(fake_docker),
            "SYSTEMCTL_CLI": str(fake_systemctl),
            "SYSTEM_MESSAGES_FILE": str(messages),
        },
    )

    assert result.returncode == 0, result.stderr
    calls = aws_log.read_text()
    assert "--metric-name BotHealthy --value 1" in calls
    assert "--metric-name OOMEvents --value 0" in calls
    assert "--metric-name HostTelemetryHealthy --value 1" in calls

from __future__ import annotations

import json
import os
import stat
import subprocess
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


def test_secret_renderer_writes_root_only_files_without_printing_values(tmp_path: Path) -> None:
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
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
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
  *'MAX(version)'*) printf '9\\n' ;;
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
  *'/deployment/version'*) printf '1.2.3\\n' ;;
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
            "DOCKER_CLI": str(fake_docker),
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
            "DOCKER_CLI": str(fake_docker),
            "OPERATIONS_BUCKET": "test-operations-bucket",
            "BACKUP_TMP_ROOT": str(tmp_path),
            "ALLOW_NON_ROOT_FOR_TESTS": "1",
        },
    )

    assert result.returncode != 0
    assert "--metric-name BackupSuccess --value 0" in aws_log.read_text()

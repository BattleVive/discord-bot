from __future__ import annotations

import os
from pathlib import Path
import subprocess


def test_migrate_secrets_initializes_runtime_tokens_without_prompting(
    tmp_path: Path,
) -> None:
    aws_log = tmp_path / "aws.log"
    fake_aws = tmp_path / "aws"
    fake_aws.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$TEST_AWS_LOG\"\n"
        "case \"$*\" in *describe-parameters*) printf 'None\\n' ;; esac\n",
        encoding="utf-8",
    )
    fake_aws.chmod(0o755)
    script = Path(__file__).parents[1] / "infra/tools/migrate-secrets.sh"
    environment = os.environ | {
        "AWS_CLI": str(fake_aws),
        "TEST_AWS_LOG": str(aws_log),
    }

    completed = subprocess.run(
        ["bash", str(script)],
        input="\n".join(f"value-{number}" for number in range(1, 7)) + "\n",
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )

    assert completed.returncode == 0, completed.stderr
    assert "tokens JSON" not in completed.stdout
    assert "--name /battlevive/production/tokens" in aws_log.read_text(
        encoding="utf-8"
    )
    assert "--value {}" in aws_log.read_text(encoding="utf-8")

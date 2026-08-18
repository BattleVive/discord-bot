import hashlib
import subprocess
import sys
import tarfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKER = ROOT / "scripts" / "release" / "bundle.py"


def create_source(root: Path):
    files = {
        "docker-compose.yml": "services: {}\n",
        "docker-compose.aws.yml": "services: {}\n",
        "scripts/deploy/deploy.sh": "#!/bin/sh\n",
        "infra/host/bin/compose": "#!/bin/sh\n",
        "infra/host/scripts/backup.sh": "#!/bin/sh\n",
        "infra/host/scripts/restore-verify.sh": "#!/bin/sh\n",
        "infra/host/systemd/battlevive.service": "[Unit]\n",
        "infra/host/ssm/install.yml": "schemaVersion: '2.2'\n",
    }
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        if path.suffix == ".sh":
            path.chmod(0o755)


def test_bundle_normalizes_compose_and_includes_verified_host_assets(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    create_source(source)
    output = tmp_path / "release.tar.gz"
    result = subprocess.run(
        [sys.executable, str(PACKER), "--root", str(source), "--output", str(output)],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == hashlib.sha256(output.read_bytes()).hexdigest()
    with tarfile.open(output) as archive:
        names = set(archive.getnames())
        assert {
            "compose.yaml",
            "compose.aws.yaml",
            "scripts/deploy.sh",
            "bin/compose",
            "scripts/backup.sh",
            "scripts/restore-verify.sh",
            "systemd/battlevive.service",
            "ssm/install.yml",
            "SHA256SUMS",
        } <= names
        sums = archive.extractfile("SHA256SUMS").read().decode()
        assert hashlib.sha256(b"services: {}\n").hexdigest() + "  compose.yaml" in sums


def test_bundle_refuses_missing_required_operational_asset(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    create_source(source)
    (source / "infra/host/scripts/backup.sh").unlink()
    result = subprocess.run(
        [sys.executable, str(PACKER), "--root", str(source), "--output", str(tmp_path / "x.tgz")],
        text=True,
        capture_output=True,
    )
    assert result.returncode != 0
    assert "infra/host/scripts/backup.sh" in result.stderr


def test_bundle_refuses_missing_required_compose_launcher(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    create_source(source)
    (source / "infra/host/bin/compose").unlink()

    result = subprocess.run(
        [sys.executable, str(PACKER), "--root", str(source), "--output", str(tmp_path / "x.tgz")],
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "infra/host/bin/compose" in result.stderr

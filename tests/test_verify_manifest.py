import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERIFY = ROOT / "scripts" / "release" / "verify_manifest.py"


def run_manifest(manifest, revision="a" * 40):
    return subprocess.run(
        [sys.executable, str(VERIFY), "--revision", revision,
         "--source", "https://github.com/BattleVive/discord-bot",
         "--version", "1.2.3"],
        input=json.dumps(manifest),
        text=True,
        capture_output=True,
    )


def valid_manifest():
    return {
        "schemaVersion": 2,
        "annotations": {
            "org.opencontainers.image.revision": "a" * 40,
            "org.opencontainers.image.source": "https://github.com/BattleVive/discord-bot",
            "org.opencontainers.image.version": "1.2.3",
        },
        "manifests": [
            {"platform": {"os": "linux", "architecture": "amd64"}},
            {"platform": {"os": "linux", "architecture": "arm64"}},
            {"platform": {"os": "unknown", "architecture": "unknown"},
             "annotations": {"vnd.docker.reference.type": "attestation-manifest"}},
            {"platform": {"os": "unknown", "architecture": "unknown"},
             "annotations": {"vnd.docker.reference.type": "attestation-manifest"}},
        ],
    }


def test_accepts_required_platforms_revision_and_attestation_entries():
    result = run_manifest(valid_manifest())
    assert result.returncode == 0, result.stderr


def test_rejects_wrong_revision():
    manifest = valid_manifest()
    manifest["annotations"]["org.opencontainers.image.revision"] = "b" * 40
    result = run_manifest(manifest)
    assert result.returncode != 0
    assert "revision" in result.stderr


def test_rejects_missing_runtime_platform():
    manifest = valid_manifest()
    manifest["manifests"] = manifest["manifests"][1:]
    result = run_manifest(manifest)
    assert result.returncode != 0
    assert "linux/amd64" in result.stderr


def test_rejects_missing_oci_identity_or_attestations():
    manifest = valid_manifest()
    del manifest["annotations"]["org.opencontainers.image.version"]
    result = run_manifest(manifest)
    assert result.returncode != 0
    assert "version" in result.stderr

    manifest = valid_manifest()
    manifest["manifests"] = manifest["manifests"][:2]
    result = run_manifest(manifest)
    assert result.returncode != 0
    assert "attestation" in result.stderr

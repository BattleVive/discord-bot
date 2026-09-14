from __future__ import annotations

import json
import hashlib
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run(script: str, payload: dict, *args: str) -> subprocess.CompletedProcess[str]:
    """Run a subprocess and return its completed result."""
    return subprocess.run(
        [sys.executable, str(ROOT / script), *args], input=json.dumps(payload), text=True,
        capture_output=True, check=False,
    )


def manifest_payload(*, digest: str = "sha256:" + "a" * 64) -> dict:
    """Create a structurally valid, checksummed release manifest."""
    item = {
        "bot_version": "2.0.0", "bundle_checksum": digest, "source_revision": "b" * 40,
        "services": {name: {"version": "1.0.0", "digest": digest}
                     for name in ("gateway-service", "upstream-service", "image-renderer")},
    }
    encoded = json.dumps(item, sort_keys=True, separators=(",", ":")).encode()
    item["manifest_checksum"] = hashlib.sha256(encoded).hexdigest()
    return item


def test_service_planner_reuses_unchanged_digest_without_push() -> None:
    """Verify that service planner reuses unchanged digest without push."""
    result = run("scripts/release/service_plan.py", {
        "changed_files": [],
        "versions": {"gateway-service": "1.0.0", "upstream-service": "1.0.0", "image-renderer": "1.0.0"},
        "previous_versions": {"gateway-service": "1.0.0", "upstream-service": "1.0.0", "image-renderer": "1.0.0"},
        "existing_digests": {"gateway-service": "sha256:" + "a" * 64},
    })
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["services"]["gateway-service"] == {"version": "1.0.0", "push": False, "digest": "sha256:" + "a" * 64}


def test_service_planner_requires_content_and_version_to_change_together() -> None:
    """Verify that service planner requires content and version to change together."""
    result = run("scripts/release/service_plan.py", {
        "changed_files": ["services/gateway/app/main.py"],
        "versions": {"gateway-service": "1.0.0", "upstream-service": "1.0.0", "image-renderer": "1.0.0"},
        "previous_versions": {"gateway-service": "1.0.0", "upstream-service": "1.0.0", "image-renderer": "1.0.0"},
    })
    assert result.returncode != 0
    assert "changed image inputs without a service-version bump" in result.stderr


def test_service_planner_rejects_a_version_only_change() -> None:
    """Verify that service planner rejects a version only change."""
    result = run("scripts/release/service_plan.py", {
        "changed_files": ["services/gateway/VERSION"],
        "versions": {"gateway-service": "1.0.1", "upstream-service": "1.0.0", "image-renderer": "1.0.0"},
        "previous_versions": {"gateway-service": "1.0.0", "upstream-service": "1.0.0", "image-renderer": "1.0.0"},
    })
    assert result.returncode != 0
    assert "service-version bump without changed image content" in result.stderr


def test_service_planner_treats_declared_shared_inputs_as_image_content() -> None:
    """Verify that service planner treats declared shared inputs as image content."""
    result = run("scripts/release/service_plan.py", {
        "changed_files": ["init-db/001.sql", "assets/icon.png"],
        "versions": {"gateway-service": "1.0.1", "upstream-service": "1.0.0", "image-renderer": "1.0.1"},
        "previous_versions": {"gateway-service": "1.0.0", "upstream-service": "1.0.0", "image-renderer": "1.0.0"},
    })
    assert result.returncode == 0, result.stderr
    services = json.loads(result.stdout)["services"]
    assert services["gateway-service"]["push"] is True
    assert services["image-renderer"]["push"] is True
    assert services["upstream-service"]["push"] is False


def test_service_planner_does_not_treat_compose_as_an_image_input() -> None:
    """Verify compose changes do not force unrelated service image releases."""
    result = run("scripts/release/service_plan.py", {
        "changed_files": ["docker-compose.yml"],
        "versions": {"gateway-service": "1.0.0", "upstream-service": "1.0.0", "image-renderer": "1.0.0"},
        "previous_versions": {"gateway-service": "1.0.0", "upstream-service": "1.0.0", "image-renderer": "1.0.0"},
    })
    assert result.returncode == 0, result.stderr
    assert all(not service["push"] for service in json.loads(result.stdout)["services"].values())


def test_service_planner_bootstraps_a_missing_registry_image() -> None:
    """Verify an unseeded GHCR service image is built without a version bump."""
    result = run("scripts/release/service_plan.py", {
        "changed_files": [],
        "versions": {"gateway-service": "1.0.0", "upstream-service": "1.0.0", "image-renderer": "1.0.0"},
        "previous_versions": {"gateway-service": "1.0.0", "upstream-service": "1.0.0", "image-renderer": "1.0.0"},
        "missing_services": ["gateway-service"],
    })
    assert result.returncode == 0, result.stderr
    services = json.loads(result.stdout)["services"]
    assert services["gateway-service"]["push"] is True
    assert services["upstream-service"]["push"] is False


def test_image_tags_include_the_service_version_and_compatible_aliases() -> None:
    """Verify a service version publishes its exact, minor, major, and latest tags."""
    result = run("scripts/release/image_tags.py", {}, "--version", "1.1.2")
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["1.1.2", "1.1", "1", "latest"]


def test_image_tags_reject_noncanonical_service_versions() -> None:
    """Verify image tag aliases require a canonical semantic service version."""
    result = run("scripts/release/image_tags.py", {}, "--version", "1.1")
    assert result.returncode != 0


def test_release_manifest_requires_all_digest_pinned_services() -> None:
    """Verify that release manifest requires all digest pinned services."""
    result = run("scripts/release/release_manifest.py", manifest_payload(), "--validate")
    assert result.returncode == 0, result.stderr


def test_release_manifest_rejects_tampering_after_checksum() -> None:
    """Verify that manifest validation rejects a stale checksum after any change."""
    item = manifest_payload()
    item["services"]["gateway-service"]["digest"] = "sha256:" + "c" * 64
    result = run("scripts/release/release_manifest.py", item, "--validate")
    assert result.returncode != 0
    assert "manifest_checksum" in result.stderr


def test_manifest_builder_writes_a_digest_pinned_manifest(tmp_path: Path) -> None:
    """Verify that manifest builder writes a digest pinned manifest."""
    bundle = tmp_path / "release.tar.gz"
    bundle.write_bytes(b"bundle")
    output = tmp_path / "manifest.json"
    digest = "sha256:" + "a" * 64
    result = subprocess.run(
        [
            sys.executable, str(ROOT / "scripts/release/build_manifest.py"),
            "--bot-version", "2.0.0", "--revision", "b" * 40,
            "--bundle", str(bundle), "--output", str(output),
            *sum((["--service", f"{name}=1.0.0={digest}"] for name in
                  ("gateway-service", "upstream-service", "image-renderer")), []),
        ], text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    manifest = json.loads(output.read_text())
    assert manifest["services"]["gateway-service"]["digest"] == digest
    assert manifest["bundle_checksum"].startswith("sha256:")
    assert len(manifest["manifest_checksum"]) == 64

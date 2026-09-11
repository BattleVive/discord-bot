"""Static contracts for the v2 blue/green infrastructure and release flow."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_named_slot_roots_instantiate_the_reusable_module_with_isolated_state() -> None:
    for slot in ("blue", "green"):
        assert '"../../modules/slot"' in read(f"infra/slots/{slot}/main.tf")
        assert f'"battlevive-bot/{slot}.tfstate"' in read(f"infra/slots/{slot}/backend.tf")


def test_slot_module_uses_rds_and_unencrypted_gp3_storage() -> None:
    compute = read("infra/modules/slot/compute.tf")
    assert 'resource "aws_db_instance" "postgres"' in compute
    assert 'instance_class' in compute and '"db.t4g.micro"' in compute
    assert "allocated_storage" in compute and "= 20" in compute
    assert "storage_type" in compute and '"gp3"' in compute
    assert "storage_encrypted" in compute and "= false" in compute
    assert "encrypted" in compute and "= false" in compute


def test_no_customer_kms_or_host_database_backups_remain() -> None:
    assert not (ROOT / "infra/modules/production/kms.tf").exists()
    assert not (ROOT / "infra/host/scripts/backup.sh").exists()
    assert not (ROOT / "infra/host/scripts/restore-verify.sh").exists()
    assert "postgres:" not in read("docker-compose.aws.yml")


def test_slots_have_isolated_parameters_and_active_control_parameter() -> None:
    ssm = read("infra/modules/slot/ssm.tf")
    production = read("infra/production/main.tf")
    locals_tf = read("infra/modules/slot/locals.tf")
    variables_tf = read("infra/modules/slot/variables.tf")
    assert 'default     = "/battlevive"' in variables_tf
    assert 'parameter_root = "${var.parameter_root}/${var.slot}"' in locals_tf
    assert 'resource "aws_ssm_parameter" "active_slot"' in production
    assert 'resource "aws_ssm_parameter" "release_candidate_slot"' in production
def test_release_uses_public_ghcr_service_images_and_manifest() -> None:
    workflow = read(".github/workflows/release.yml")
    assert "ghcr.io/BattleVive/gateway-service" in workflow
    assert "ghcr.io/BattleVive/upstream-service" in workflow
    assert "ghcr.io/BattleVive/image-renderer" in workflow
    assert "GITHUB_TOKEN" in workflow
    assert "scripts/release/release_manifest.py" in workflow
    assert "docker.io" not in workflow


def test_services_have_initial_versions_and_release_planner() -> None:
    for service in ("gateway", "upstream-data", "image-renderer"):
        assert (ROOT / "services" / service / "VERSION").read_text().strip() == "1.0.0"
    planner = read("scripts/release/service_plan.py")
    assert "changed image inputs without a service-version bump" in planner
    assert "service-version bump without changed image content" in planner

"""Static contracts for the v2 blue/green infrastructure and release flow."""

from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    """Read text from a repository-relative path."""
    return (ROOT / path).read_text(encoding="utf-8")


def test_named_slot_roots_instantiate_the_reusable_module_with_isolated_state() -> None:
    """Verify that named slot roots instantiate the reusable module with isolated state."""
    for slot in ("blue", "green"):
        assert '"../../modules/slot"' in read(f"infra/slots/{slot}/main.tf")
        assert f'"battlevive-bot/{slot}.tfstate"' in read(f"infra/slots/{slot}/backend.tf")


def test_bootstrap_provisions_a_persistent_state_bucket_for_each_slot() -> None:
    """Verify blue and green retain independent Terraform state buckets."""
    bootstrap = read("infra/bootstrap/main.tf")
    outputs = read("infra/bootstrap/outputs.tf")
    assert 'toset(["blue", "green"])' in bootstrap
    assert 'resource "aws_s3_bucket" "slot_state"' in bootstrap
    assert 'prevent_destroy = true' in bootstrap
    assert 'output "slot_state_buckets"' in outputs


def test_no_separate_e2e_topology_remains() -> None:
    """Verify release slots are the only deployable bot topology."""
    workflow = read(".github/workflows/infrastructure.yml")
    assert not (ROOT / "infra/e2e").exists()
    assert "e2e" not in workflow
    assert "TF_BLUE_STATE_BUCKET" in workflow
    assert "TF_GREEN_STATE_BUCKET" in workflow


def test_release_creates_only_the_inactive_slot_with_the_staging_token() -> None:
    """Verify a release stages its candidate without replacing the active bot."""
    workflow = read(".github/workflows/release.yml")
    assert "stage-inactive-slot" in workflow
    assert "BOT_TOKEN_STAGING" in workflow
    assert "/battlevive/production/control/active-slot" in workflow
    assert "TF_BLUE_STATE_BUCKET" in workflow
    assert "TF_GREEN_STATE_BUCKET" in workflow


def test_promotion_health_gates_the_active_slot_switch_and_delays_cleanup() -> None:
    """Verify promotion deploys successfully before switching production control."""
    workflow = read(".github/workflows/promote.yml")
    cleanup = read(".github/workflows/retire-slot.yml")
    assert "deploy-production-candidate" in workflow
    assert "needs: [prepare-candidate, deploy-production-candidate]" in workflow
    assert "BOT_TOKEN" in workflow and "BOT_TOKEN_STAGING" in workflow
    assert "+6 hours" in workflow
    assert "schedule:" in cleanup
    assert "terraform -chdir=\"infra/slots/$retired_slot\" destroy" in cleanup


def test_every_slot_transition_uses_one_shared_lock() -> None:
    """Verify staging, promotion, and retirement cannot overlap."""
    for workflow in (".github/workflows/release.yml", ".github/workflows/promote.yml", ".github/workflows/retire-slot.yml"):
        assert re.search(r"^concurrency:\n  group: slot-transition$", read(workflow), re.MULTILINE)


def test_manual_slot_destruction_rechecks_live_retirement_control() -> None:
    """Verify an operator cannot destroy a live or unretired slot."""
    workflow = read(".github/workflows/infrastructure.yml")
    assert "/battlevive/production/control/retired-slot" in workflow
    assert "/battlevive/production/control/active-slot" in workflow
    assert '[[ "$selected_slot" == "$retired_slot" ]]' in workflow
    assert '[[ "$selected_slot" != "$active_slot" ]]' in workflow
    assert "github.event.inputs.action == 'destroy' && 'slot-transition'" in workflow


def test_retired_slot_cannot_equal_the_active_slot() -> None:
    """Verify production control rejects a request to retire the live slot."""
    production = read("infra/production/main.tf")
    retired_parameter = production.split('resource "aws_ssm_parameter" "retired_slot"', maxsplit=1)[1]
    assert "var.retired_slot != var.active_slot" in retired_parameter


def test_slot_module_uses_rds_and_unencrypted_gp3_storage() -> None:
    """Verify that slot module uses RDS and unencrypted gp3 storage."""
    compute = read("infra/modules/slot/compute.tf")
    assert 'resource "aws_db_instance" "postgres"' in compute
    assert 'instance_class' in compute and '"db.t4g.micro"' in compute
    assert "allocated_storage" in compute and "= 20" in compute
    assert "storage_type" in compute and '"gp3"' in compute
    assert re.search(r"^\s*storage_encrypted\s+=\s+false\s*$", compute, re.MULTILINE)
    assert re.search(r"^\s*encrypted\s+=\s+false\s*$", compute, re.MULTILINE)
    assert re.search(r'^\s*db_name\s+=\s+var\.rds_snapshot_identifier == null \? "battlevive" : null\s*$', compute, re.MULTILINE)
    assert 'http_tokens = "required"' in compute


def test_no_customer_kms_or_host_database_backups_remain() -> None:
    """Verify that no customer kms or host database backups remain."""
    assert not (ROOT / "infra/modules/production/kms.tf").exists()
    assert not (ROOT / "infra/host/scripts/backup.sh").exists()
    assert not (ROOT / "infra/host/scripts/restore-verify.sh").exists()
    assert "postgres:" not in read("docker-compose.aws.yml")


def test_slots_have_isolated_parameters_and_active_control_parameter() -> None:
    """Verify that slots have isolated parameters and active control parameter."""
    ssm = read("infra/modules/slot/ssm.tf")
    production = read("infra/production/main.tf")
    locals_tf = read("infra/modules/slot/locals.tf")
    variables_tf = read("infra/modules/slot/variables.tf")
    assert 'default     = "/battlevive"' in variables_tf
    assert 'parameter_root = "${var.parameter_root}/${var.slot}"' in locals_tf
    assert 'resource "aws_ssm_parameter" "active_slot"' in production
    assert 'resource "aws_ssm_parameter" "release_candidate_slot"' in production


def test_slot_deploy_document_passes_its_slot_to_the_deploy_script() -> None:
    """Verify the SSM document satisfies deploy.sh's required slot argument."""
    deploy = read("infra/modules/slot/deploy.tf")
    assert 'deploy --slot \\"${var.slot}\\" --manifest \\"$manifest\\"' in deploy
def test_release_uses_public_ghcr_service_images_and_manifest() -> None:
    """Verify that release uses public GHCR service images and manifest."""
    workflow = read(".github/workflows/release.yml")
    assert "ghcr.io/battlevive/gateway-service" in workflow
    assert "ghcr.io/battlevive/upstream-service" in workflow
    assert "ghcr.io/battlevive/image-renderer" in workflow
    assert "GITHUB_TOKEN" in workflow
    assert "scripts/release/release_manifest.py" in workflow
    assert "docker.io" not in workflow


def test_release_publishes_multi_architecture_semantic_image_aliases() -> None:
    """Verify each changed service publishes portable semantic image tags."""
    workflow = read(".github/workflows/release.yml")
    assert "platforms: linux/amd64,linux/arm64" in workflow
    assert "scripts/release/image_tags.py" in workflow
    assert "tags: ${{ steps.tags.outputs.value }}" in workflow
    assert "--missing-service" in workflow


def test_infrastructure_scan_loads_documented_trivy_exceptions() -> None:
    """Verify expected infrastructure exceptions are passed to Trivy CI."""
    workflow = read(".github/workflows/infrastructure.yml")
    ignored = read("infra/.trivyignore")
    assert "trivyignores: infra/.trivyignore" in workflow
    assert "AVD-AWS-0080" in ignored
    assert "AVD-AWS-0177" not in ignored


def test_terraform_blocks_bucket_public_access_and_avoids_subnet_auto_public_ips() -> None:
    """Verify the static scan's non-encryption findings are fixed in Terraform."""
    compute = read("infra/modules/slot/compute.tf")
    production = read("infra/production/main.tf")
    assert "map_public_ip_on_launch = false" in compute
    assert "associate_public_ip_address = true" in compute
    assert 'resource "aws_s3_bucket_public_access_block" "operations"' in production
    for setting in ("block_public_acls", "block_public_policy", "ignore_public_acls", "restrict_public_buckets"):
        assert re.search(rf"^\s*{setting}\s+=\s+true\s*$", production, re.MULTILINE)


def test_local_compose_defaults_to_release_ghcr_service_images() -> None:
    """Verify local deployments pull the service images published by release."""
    compose = read("docker-compose.yml")
    assert "${UPSTREAM_DATA_IMAGE:-ghcr.io/battlevive/upstream-service:latest}" in compose
    assert compose.count("${GATEWAY_IMAGE:-ghcr.io/battlevive/gateway-service:latest}") == 2
    assert "${IMAGE_RENDERER_IMAGE:-ghcr.io/battlevive/image-renderer:latest}" in compose
    assert "voxix/battlevive-" not in compose


def test_services_have_initial_versions_and_release_planner() -> None:
    """Verify that services have initial versions and release planner."""
    for service in ("gateway", "upstream-data", "image-renderer"):
        assert (ROOT / "services" / service / "VERSION").read_text().strip() == "1.0.0"
    planner = read("scripts/release/service_plan.py")
    assert "changed image inputs without a service-version bump" in planner
    assert "service-version bump without changed image content" in planner

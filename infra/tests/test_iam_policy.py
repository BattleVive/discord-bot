from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_apply_role_can_read_only_project_ssm_documents() -> None:
    source = (ROOT / "infra/modules/production/iam.tf").read_text(encoding="utf-8")
    apply_policy = source.split(
        'data "aws_iam_policy_document" "apply" {', 1
    )[1].split('resource "aws_iam_role_policy" "github_apply"', 1)[0]

    assert '"ReadProjectSSMDocuments"' in apply_policy
    assert '"ssm:GetDocument"' in apply_policy
    assert (
        '"arn:${data.aws_partition.current.partition}:ssm:${var.aws_region}:${var.account_id}:document/${local.name}-*"'
        in apply_policy
    )


def test_instance_role_can_read_supabase_url_runtime_configuration() -> None:
    source = (ROOT / "infra/modules/production/iam.tf").read_text(encoding="utf-8")
    instance_policy = source.split(
        'data "aws_iam_policy_document" "instance" {', 1
    )[1].split('resource "aws_iam_role_policy" "instance"', 1)[0]
    runtime_configuration = instance_policy.split(
        'sid     = "ReadRuntimeConfiguration"', 1
    )[1].split('\n  }', 1)[0]

    assert "aws_ssm_parameter.supabase_url.arn" in runtime_configuration


def test_alert_topic_uses_cloudwatch_compatible_customer_managed_kms_key() -> None:
    monitoring = (ROOT / "infra/modules/production/monitoring.tf").read_text(encoding="utf-8")
    kms = (ROOT / "infra/modules/production/kms.tf").read_text(encoding="utf-8")

    assert 'resource "aws_kms_key" "alerts"' in kms
    assert '"cloudwatch.amazonaws.com"' in kms
    assert '"kms:GenerateDataKey*"' in kms
    assert '"kms:Decrypt"' in kms
    assert 'kms_master_key_id = aws_kms_key.alerts.arn' in monitoring
    assert 'kms_master_key_id = "alias/aws/sns"' not in monitoring


def test_ci_roles_have_only_required_alert_kms_permissions() -> None:
    source = (ROOT / "infra/modules/production/iam.tf").read_text(encoding="utf-8")
    plan_policy = source.split(
        'data "aws_iam_policy_document" "plan" {', 1
    )[1].split('data "aws_iam_policy_document" "plan_with_state"', 1)[0]
    apply_policy = source.split(
        'data "aws_iam_policy_document" "apply" {', 1
    )[1].split('resource "aws_iam_role_policy" "github_apply"', 1)[0]

    assert '"kms:DescribeKey"' in plan_policy
    assert '"kms:GetKeyPolicy"' in plan_policy
    assert 'resources = [aws_kms_key.alerts.arn]' in plan_policy
    assert '"CreateTaggedKMSKey"' in apply_policy
    assert '"kms:CreateKey"' in apply_policy
    assert '"aws:RequestTag/Project"' in apply_policy
    assert '"ManageProjectKMSKey"' in apply_policy
    assert '"ManageProjectKMSAlias"' in apply_policy

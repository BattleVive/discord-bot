from __future__ import annotations

from pathlib import Path
import re


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


def test_bootstrap_tokens_use_write_only_terraform_inputs_and_narrow_ci_access() -> None:
    versions = (ROOT / "infra/production/versions.tf").read_text(encoding="utf-8")
    storage = (ROOT / "infra/modules/production/storage.tf").read_text(encoding="utf-8")
    source = (ROOT / "infra/modules/production/iam.tf").read_text(encoding="utf-8")
    plan_policy = source.split(
        'data "aws_iam_policy_document" "plan" {', 1
    )[1].split('data "aws_iam_policy_document" "plan_with_state"', 1)[0]
    apply_policy = source.split(
        'data "aws_iam_policy_document" "apply" {', 1
    )[1].split('resource "aws_iam_role_policy" "github_apply"', 1)[0]

    assert 'required_version = ">= 1.11.0"' in versions
    assert 'resource "aws_ssm_parameter" "bootstrap_jwt"' in storage
    assert 'resource "aws_ssm_parameter" "bootstrap_refresh_token"' in storage
    assert "value_wo         = var.bootstrap_jwt" in storage
    assert "value_wo_version = var.bootstrap_token_generation" in storage
    assert "value             = var.bootstrap_jwt" not in storage
    assert "prevent_destroy = true" in storage
    assert 'source  = "hashicorp/time"' in versions
    assert 'resource "time_sleep" "bootstrap_token_permission_propagation"' in storage
    assert 'create_duration = "60s"' in storage
    assert "policy = aws_iam_role_policy.github_apply.policy" in storage
    assert "depends_on = [time_sleep.bootstrap_token_permission_propagation]" in storage

    expected_parameter_arns = {
        '"arn:${data.aws_partition.current.partition}:ssm:${var.aws_region}:${var.account_id}:parameter${local.secret_parameter_names.bootstrap_jwt}"',
        '"arn:${data.aws_partition.current.partition}:ssm:${var.aws_region}:${var.account_id}:parameter${local.secret_parameter_names.bootstrap_refresh_token}"',
    }
    for policy in (plan_policy, apply_policy):
        assert '"ssm:GetParameter"' in policy
        assert expected_parameter_arns <= set(
            line.strip().rstrip(",") for line in policy.splitlines()
        )

    assert '"ssm:PutParameter"' in apply_policy
    assert '"ssm:AddTagsToResource"' in apply_policy
    assert '"ssm:RemoveTagsFromResource"' in apply_policy


def test_github_oidc_roles_use_only_same_environment_transfer_bridge_subjects() -> None:
    variables = (ROOT / "infra/production/variables.tf").read_text(encoding="utf-8")
    main = (ROOT / "infra/production/main.tf").read_text(encoding="utf-8")
    module_variables = (ROOT / "infra/modules/production/variables.tf").read_text(
        encoding="utf-8"
    )
    source = (ROOT / "infra/modules/production/iam.tf").read_text(encoding="utf-8")
    fixtures = [
        (ROOT / "infra/modules/production/tests/adoption.tftest.hcl").read_text(
            encoding="utf-8"
        ),
        (ROOT / "infra/modules/production/tests/steady_state.tftest.hcl").read_text(
            encoding="utf-8"
        ),
    ]

    expected_subjects = {
        "infrastructure_plan": {
            "repo:voxix-dev/battlevive-bot:environment:infrastructure-plan",
            "repo:BattleVive@325350336/discord-bot@1295590282:environment:infrastructure-plan",
            "repo:voxix-dev@203257071/battlevive-bot@1295590282:environment:infrastructure-plan",
        },
        "infrastructure_apply": {
            "repo:voxix-dev/battlevive-bot:environment:infrastructure-apply",
            "repo:BattleVive@325350336/discord-bot@1295590282:environment:infrastructure-apply",
            "repo:voxix-dev@203257071/battlevive-bot@1295590282:environment:infrastructure-apply",
        },
        "production": {
            "repo:voxix-dev/battlevive-bot:environment:production",
            "repo:BattleVive@325350336/discord-bot@1295590282:environment:production",
            "repo:voxix-dev@203257071/battlevive-bot@1295590282:environment:production",
        },
    }
    assert 'variable "github_oidc_subjects" {' in variables
    assert 'variable "github_repository" {' not in variables
    assert re.search(
        r"^\s*github_oidc_subjects\s+= var\.github_oidc_subjects$", main, re.MULTILINE
    )
    assert 'variable "github_oidc_subjects" {' in module_variables
    assert 'variable "github_repository" {' not in module_variables

    def assert_subjects_type_contract(source: str) -> None:
        variable = re.search(
            r'^variable "github_oidc_subjects" \{(?P<body>.*?)^\}',
            source,
            re.MULTILINE | re.DOTALL,
        )
        assert variable is not None
        object_type = re.search(
            r'^\s*type\s*=\s*object\(\{(?P<attributes>.*?)^\s*\}\)',
            variable.group("body"),
            re.MULTILINE | re.DOTALL,
        )
        assert object_type is not None
        assert dict(
            re.findall(
                r"^\s*([a-z_]+)\s*=\s*(.+?)\s*$",
                object_type.group("attributes"),
                re.MULTILINE,
            )
        ) == {
            "infrastructure_plan": "set(string)",
            "infrastructure_apply": "set(string)",
            "production": "set(string)",
        }

    assert_subjects_type_contract(variables)
    assert_subjects_type_contract(module_variables)

    def assert_subject_sets(subject_sets: str) -> None:
        for role, subjects in expected_subjects.items():
            subject_set = re.search(
                rf"^\s*{role}\s*=\s*\[(.*?)^\s*\]",
                subject_sets,
                re.MULTILINE | re.DOTALL,
            )
            assert subject_set is not None
            actual_subjects = re.findall(r'"([^"]+)"', subject_set.group(1))
            assert len(actual_subjects) == 3
            assert all(subject in subjects for subject in actual_subjects)
            assert set(actual_subjects) == subjects

    oidc_subjects = variables.split('variable "github_oidc_subjects" {', 1)[1].split(
        'variable "github_oidc_provider_arn" {', 1
    )[0]
    assert_subject_sets(oidc_subjects)

    for fixture in fixtures:
        assert "github_repository" not in fixture
        fixture_subjects = re.search(
            r"github_oidc_subjects\s*=\s*\{(.*?)^\s*\}",
            fixture,
            re.MULTILINE | re.DOTALL,
        )
        assert fixture_subjects is not None
        assert_subject_sets(fixture_subjects.group(1))

    for role in expected_subjects:
        policy_name = {
            "infrastructure_plan": "github_plan_assume",
            "infrastructure_apply": "github_apply_assume",
            "production": "github_deploy_assume",
        }[role]
        policy = source.split(
            f'data "aws_iam_policy_document" "{policy_name}" {{', 1
        )[1].split("\n}", 1)[0]
        statements = re.findall(r"^  statement \{$", policy, re.MULTILINE)
        conditions = re.findall(
            r"^    condition \{\n(.*?)^    \}",
            policy,
            re.MULTILINE | re.DOTALL,
        )
        actual_conditions = {
            (
                re.search(r'^\s*test\s+=\s+"([^"]+)"$', condition, re.MULTILINE)[
                    1
                ],
                re.search(
                    r'^\s*variable\s+=\s+"([^"]+)"$', condition, re.MULTILINE
                )[1],
                re.search(r"^\s*values\s+=\s+(.+)$", condition, re.MULTILINE)[1],
            )
            for condition in conditions
        }
        expected_conditions = {
            (
                "StringEquals",
                "${local.oidc_hostpath}:aud",
                '["sts.amazonaws.com"]',
            ),
            (
                "StringEquals",
                "${local.oidc_hostpath}:sub",
                f"var.github_oidc_subjects.{role}",
            ),
        }

        assert len(statements) == 1
        assert "StringLike" not in policy
        assert "*" not in policy
        assert "repository_owner" not in policy.lower()
        assert len(conditions) == 2
        assert actual_conditions == expected_conditions
        assert policy.count("values   = var.github_oidc_subjects.") == 1

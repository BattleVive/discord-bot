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

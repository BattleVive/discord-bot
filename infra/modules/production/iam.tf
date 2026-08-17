data "aws_partition" "current" {}

resource "aws_iam_openid_connect_provider" "github" {
  count = var.github_oidc_provider_arn == null ? 1 : 0

  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]

  tags = local.common_tags
}

data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "instance" {
  name               = var.instance_role_name
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json

  lifecycle { prevent_destroy = true }
  tags = local.common_tags
}

resource "aws_iam_instance_profile" "bot" {
  name = var.instance_profile_name
  role = aws_iam_role.instance.name

  lifecycle { prevent_destroy = true }
  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

data "aws_iam_policy_document" "instance" {
  statement {
    sid = "ReadRuntimeParameters"
    actions = [
      "ssm:GetParameter",
      "ssm:GetParameters",
    ]
    resources = [
      for name in values(local.secret_parameter_names) :
      "arn:${data.aws_partition.current.partition}:ssm:${var.aws_region}:${var.account_id}:parameter${name}"
    ]
  }

  statement {
    sid     = "WriteOnlyRotatingAndDeploymentState"
    actions = ["ssm:PutParameter"]
    resources = [
      "arn:${data.aws_partition.current.partition}:ssm:${var.aws_region}:${var.account_id}:parameter${local.secret_parameter_names.rotating_tokens}",
      "arn:${data.aws_partition.current.partition}:ssm:${var.aws_region}:${var.account_id}:parameter${local.parameter_root}/deployment/*",
    ]
  }

  statement {
    sid       = "ListOperationsBucket"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.operations.arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["backups/*", "releases/*"]
    }
  }

  statement {
    sid = "UseOperationsObjects"
    actions = [
      "s3:GetObject",
      "s3:GetObjectVersion",
      "s3:PutObject",
      "s3:AbortMultipartUpload",
    ]
    resources = [
      "${aws_s3_bucket.operations.arn}/backups/*",
      "${aws_s3_bucket.operations.arn}/releases/*",
    ]
  }

  statement {
    sid       = "PublishOperationalMetrics"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = ["Battlevive/Production", "CWAgent"]
    }
  }

  statement {
    sid = "PublishProjectLogs"
    actions = [
      "logs:CreateLogStream",
      "logs:DescribeLogStreams",
      "logs:PutLogEvents",
    ]
    resources = [for group in aws_cloudwatch_log_group.production : "${group.arn}:*"]
  }
}

resource "aws_iam_role_policy" "instance" {
  name   = "${local.name}-runtime"
  role   = aws_iam_role.instance.id
  policy = data.aws_iam_policy_document.instance.json
}

data "aws_iam_policy_document" "github_plan_assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [local.oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_hostpath}:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_hostpath}:sub"
      values   = ["repo:${var.github_repository}:environment:infrastructure-plan"]
    }
  }
}

data "aws_iam_policy_document" "github_apply_assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [local.oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_hostpath}:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_hostpath}:sub"
      values   = ["repo:${var.github_repository}:environment:infrastructure-apply"]
    }
  }
}

data "aws_iam_policy_document" "github_deploy_assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [local.oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_hostpath}:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_hostpath}:sub"
      values   = ["repo:${var.github_repository}:environment:production"]
    }
  }
}

resource "aws_iam_role" "github_plan" {
  name               = "${local.name}-github-plan"
  assume_role_policy = data.aws_iam_policy_document.github_plan_assume.json
  tags               = local.common_tags
}

resource "aws_iam_role" "github_apply" {
  name               = "${local.name}-github-apply"
  assume_role_policy = data.aws_iam_policy_document.github_apply_assume.json
  tags               = local.common_tags
}

resource "aws_iam_role" "github_deploy" {
  name               = "${local.name}-github-deploy"
  assume_role_policy = data.aws_iam_policy_document.github_deploy_assume.json
  tags               = local.common_tags
}

data "aws_iam_policy_document" "state_access" {
  statement {
    actions   = ["s3:ListBucket"]
    resources = ["arn:${data.aws_partition.current.partition}:s3:::${var.state_bucket_name}"]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["battlevive-bot/production.tfstate*"]
    }
  }
  statement {
    actions = ["s3:GetObject", "s3:PutObject"]
    resources = [
      "arn:${data.aws_partition.current.partition}:s3:::${var.state_bucket_name}/battlevive-bot/production.tfstate",
    ]
  }
  statement {
    actions = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = [
      "arn:${data.aws_partition.current.partition}:s3:::${var.state_bucket_name}/battlevive-bot/production.tfstate.tflock",
    ]
  }
}

# The state bucket ARN is attached separately by bootstrap because its random
# suffix is intentionally unknown to this stack. This policy is infrastructure
# inspection only; it cannot mutate resources.
data "aws_iam_policy_document" "plan" {
  statement {
    actions = [
      "cloudwatch:Describe*", "cloudwatch:Get*", "cloudwatch:List*",
      "ec2:Describe*", "iam:Get*", "iam:List*", "logs:Describe*",
      "sns:Get*", "sns:List*", "ssm:Describe*", "ssm:List*", "sts:GetCallerIdentity",
    ]
    resources = ["*"]
  }

  statement {
    actions = ["s3:GetBucket*", "s3:ListBucket", "s3:GetObject"]
    resources = [
      aws_s3_bucket.operations.arn,
      "${aws_s3_bucket.operations.arn}/*",
    ]
  }

  statement {
    actions = ["ssm:GetDocument", "ssm:GetParameter", "ssm:GetParameters"]
    resources = [
      "arn:${data.aws_partition.current.partition}:ssm:${var.aws_region}:${var.account_id}:document/${local.name}-*",
      "arn:${data.aws_partition.current.partition}:ssm:${var.aws_region}:${var.account_id}:document/battlevive-production-shell",
      "arn:${data.aws_partition.current.partition}:ssm:${var.aws_region}:${var.account_id}:parameter${local.parameter_root}/config/*",
      "arn:${data.aws_partition.current.partition}:ssm:${var.aws_region}:${var.account_id}:parameter${local.parameter_root}/deployment/*",
    ]
  }
}

data "aws_iam_policy_document" "plan_with_state" {
  source_policy_documents = [
    data.aws_iam_policy_document.plan.json,
    data.aws_iam_policy_document.state_access.json,
  ]
}

resource "aws_iam_role_policy" "github_plan" {
  name   = "${local.name}-read-only-plan"
  role   = aws_iam_role.github_plan.id
  policy = data.aws_iam_policy_document.plan_with_state.json
}

data "aws_iam_policy_document" "apply" {
  statement {
    sid = "ManageProjectResources"
    actions = [
      "cloudwatch:DeleteAlarms", "cloudwatch:PutDashboard", "cloudwatch:PutMetricAlarm",
      "ec2:CreateTags", "ec2:ModifyInstanceAttribute", "ec2:ModifyVolume", "ec2:RevokeSecurityGroupIngress",
      "events:*", "iam:CreatePolicyVersion", "iam:DeletePolicyVersion", "iam:PassRole",
      "iam:PutRolePolicy", "iam:DeleteRolePolicy", "iam:UpdateAssumeRolePolicy",
      "logs:*", "s3:PutBucket*", "s3:PutLifecycleConfiguration", "sns:*", "ssm:*",
    ]
    resources = ["*"]
    condition {
      test     = "StringEqualsIfExists"
      variable = "aws:ResourceTag/Project"
      values   = ["battlevive-bot"]
    }
  }

  statement {
    sid = "CreateWithProjectTags"
    actions = [
      "cloudwatch:PutMetricAlarm", "iam:CreateRole", "iam:CreatePolicy",
      "logs:CreateLogGroup", "s3:CreateBucket", "sns:CreateTopic",
      "ssm:CreateAssociation", "ssm:CreateDocument", "ssm:PutParameter",
    ]
    resources = ["*"]
    condition {
      test     = "StringEqualsIfExists"
      variable = "aws:RequestTag/Project"
      values   = ["battlevive-bot"]
    }
  }

  statement {
    sid       = "ReadForApply"
    actions   = ["cloudwatch:Describe*", "ec2:Describe*", "iam:Get*", "iam:List*", "logs:Describe*", "sns:Get*", "sns:List*", "ssm:Describe*", "ssm:List*", "sts:GetCallerIdentity"]
    resources = ["*"]
  }


  statement {
    actions = ["s3:GetBucket*", "s3:ListBucket", "s3:GetObject"]
    resources = [
      aws_s3_bucket.operations.arn,
      "${aws_s3_bucket.operations.arn}/*",
    ]
  }

  statement {
    actions = ["ssm:GetDocument", "ssm:GetParameter", "ssm:GetParameters"]
    resources = [
      "arn:${data.aws_partition.current.partition}:ssm:${var.aws_region}:${var.account_id}:document/${local.name}-*",
      "arn:${data.aws_partition.current.partition}:ssm:${var.aws_region}:${var.account_id}:document/battlevive-production-shell",
      "arn:${data.aws_partition.current.partition}:ssm:${var.aws_region}:${var.account_id}:parameter${local.parameter_root}/config/*",
      "arn:${data.aws_partition.current.partition}:ssm:${var.aws_region}:${var.account_id}:parameter${local.parameter_root}/deployment/*",
    ]
  }
}

resource "aws_iam_role_policy" "github_apply" {
  name   = "${local.name}-scoped-apply"
  role   = aws_iam_role.github_apply.id
  policy = data.aws_iam_policy_document.apply_with_state.json
}

data "aws_iam_policy_document" "apply_with_state" {
  source_policy_documents = [
    data.aws_iam_policy_document.apply.json,
    data.aws_iam_policy_document.state_access.json,
  ]
}

data "aws_iam_policy_document" "deploy" {
  statement {
    actions   = ["s3:PutObject", "s3:GetObject"]
    resources = ["${aws_s3_bucket.operations.arn}/releases/*"]
  }
  statement {
    actions   = ["ssm:SendCommand"]
    resources = [aws_instance.bot.arn, aws_ssm_document.deploy.arn]
  }
  statement {
    actions   = ["ssm:GetCommandInvocation", "ssm:ListCommandInvocations"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "github_deploy" {
  name   = "${local.name}-release-deploy"
  role   = aws_iam_role.github_deploy.id
  policy = data.aws_iam_policy_document.deploy.json
}

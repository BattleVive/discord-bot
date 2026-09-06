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
    sid     = "ReadRuntimeConfiguration"
    actions = ["ssm:GetParameter", "ssm:GetParameters"]
    resources = [
      aws_ssm_parameter.cloudwatch_agent_config.arn,
      aws_ssm_parameter.operations_bucket.arn,
      aws_ssm_parameter.supabase_url.arn,
      aws_ssm_parameter.deployment_state.arn,
    ]
  }

  statement {
    sid     = "WriteOnlyRotatingAndDeploymentState"
    actions = ["ssm:PutParameter"]
    resources = [
      "arn:${data.aws_partition.current.partition}:ssm:${var.aws_region}:${var.account_id}:parameter${local.secret_parameter_names.rotating_tokens}",
      aws_ssm_parameter.deployment_state.arn,
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
    sid = "SSMAgentControlPlane"
    actions = [
      "ssm:DescribeAssociation",
      "ssm:DescribeDocument",
      "ssm:GetDeployablePatchSnapshotForInstance",
      "ssm:GetDocument",
      "ssm:GetManifest",
      "ssm:ListAssociations",
      "ssm:ListInstanceAssociations",
      "ssm:PutComplianceItems",
      "ssm:PutConfigurePackageResult",
      "ssm:PutInventory",
      "ssm:UpdateAssociationStatus",
      "ssm:UpdateInstanceAssociationStatus",
      "ssm:UpdateInstanceInformation",
    ]
    resources = ["*"]
  }

  statement {
    sid = "SSMMessageChannels"
    actions = [
      "ssmmessages:CreateControlChannel",
      "ssmmessages:CreateDataChannel",
      "ssmmessages:OpenControlChannel",
      "ssmmessages:OpenDataChannel",
      "ec2messages:AcknowledgeMessage",
      "ec2messages:DeleteMessage",
      "ec2messages:FailMessage",
      "ec2messages:GetEndpoint",
      "ec2messages:GetMessages",
      "ec2messages:SendReply",
    ]
    resources = ["*"]
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

  statement {
    sid       = "DiscoverProjectLogGroups"
    actions   = ["logs:DescribeLogGroups"]
    resources = ["*"]
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
      values   = ["repo:${var.github_oidc_repository}:environment:infrastructure-plan"]
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
      values   = ["repo:${var.github_oidc_repository}:environment:infrastructure-apply"]
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
      values   = ["repo:${var.github_oidc_repository}:environment:production"]
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

data "aws_iam_policy_document" "state_lock_access" {
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
    actions = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = [
      "arn:${data.aws_partition.current.partition}:s3:::${var.state_bucket_name}/battlevive-bot/production.tfstate.tflock",
    ]
  }
}

data "aws_iam_policy_document" "state_plan_access" {
  source_policy_documents = [data.aws_iam_policy_document.state_lock_access.json]
  statement {
    actions   = ["s3:GetObject"]
    resources = ["arn:${data.aws_partition.current.partition}:s3:::${var.state_bucket_name}/battlevive-bot/production.tfstate"]
  }
}

data "aws_iam_policy_document" "state_apply_access" {
  source_policy_documents = [data.aws_iam_policy_document.state_lock_access.json]
  statement {
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["arn:${data.aws_partition.current.partition}:s3:::${var.state_bucket_name}/battlevive-bot/production.tfstate"]
  }
}

# The state bucket ARN is attached separately by bootstrap because its random
# suffix is intentionally unknown to this stack. This policy is infrastructure
# inspection only; it cannot mutate resources.
data "aws_iam_policy_document" "plan" {
  statement {
    actions = [
      "cloudwatch:Describe*", "cloudwatch:Get*", "cloudwatch:List*",
      "ec2:Describe*", "iam:Get*", "iam:List*", "logs:Describe*", "logs:ListTagsForResource",
      "kms:ListAliases", "kms:ListKeys",
      "sns:Get*", "sns:List*", "ssm:Describe*", "ssm:GetMaintenanceWindow*", "ssm:List*", "sts:GetCallerIdentity",
    ]
    resources = ["*"]
  }

  statement {
    actions = [
      "kms:DescribeKey", "kms:GetKeyPolicy", "kms:GetKeyRotationStatus", "kms:ListResourceTags",
    ]
    resources = [aws_kms_key.alerts.arn]
  }

  statement {
    actions = ["s3:Get*", "s3:ListBucket"]
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

  statement {
    sid     = "ReadBootstrapTokenParameters"
    actions = ["ssm:GetParameter"]
    resources = [
      "arn:${data.aws_partition.current.partition}:ssm:${var.aws_region}:${var.account_id}:parameter${local.secret_parameter_names.bootstrap_jwt}",
      "arn:${data.aws_partition.current.partition}:ssm:${var.aws_region}:${var.account_id}:parameter${local.secret_parameter_names.bootstrap_refresh_token}",
    ]
  }
}

data "aws_iam_policy_document" "plan_with_state" {
  source_policy_documents = [
    data.aws_iam_policy_document.plan.json,
    data.aws_iam_policy_document.state_plan_access.json,
  ]
}

resource "aws_iam_role_policy" "github_plan" {
  name   = "${local.name}-read-only-plan"
  role   = aws_iam_role.github_plan.id
  policy = data.aws_iam_policy_document.plan_with_state.json
}

data "aws_iam_policy_document" "apply" {
  statement {
    sid = "ReadForApply"
    actions = [
      "cloudwatch:Describe*", "cloudwatch:Get*", "cloudwatch:List*",
      "ec2:Describe*", "iam:Get*", "iam:List*", "logs:Describe*", "logs:ListTagsForResource",
      "kms:ListAliases", "kms:ListKeys",
      "sns:Get*", "sns:List*", "ssm:Describe*", "ssm:GetMaintenanceWindow*", "ssm:List*", "sts:GetCallerIdentity",
    ]
    resources = ["*"]
  }

  statement {
    sid = "ReadProjectKMSKey"
    actions = [
      "kms:DescribeKey", "kms:GetKeyPolicy", "kms:GetKeyRotationStatus", "kms:ListResourceTags",
    ]
    resources = [aws_kms_key.alerts.arn]
  }

  statement {
    sid     = "ReadProjectSSMDocuments"
    actions = ["ssm:GetDocument"]
    resources = [
      "arn:${data.aws_partition.current.partition}:ssm:${var.aws_region}:${var.account_id}:document/${local.name}-*",
    ]
  }

  statement {
    sid = "ManageNamedIAMResources"
    actions = [
      "iam:CreateRole", "iam:DeleteRole", "iam:TagRole", "iam:UntagRole",
      "iam:UpdateAssumeRolePolicy", "iam:PutRolePolicy", "iam:DeleteRolePolicy",
      "iam:CreateInstanceProfile", "iam:DeleteInstanceProfile", "iam:AddRoleToInstanceProfile",
      "iam:RemoveRoleFromInstanceProfile", "iam:TagInstanceProfile", "iam:UntagInstanceProfile",
      "iam:PassRole",
    ]
    resources = [
      "arn:${data.aws_partition.current.partition}:iam::${var.account_id}:role/${local.name}-*",
      "arn:${data.aws_partition.current.partition}:iam::${var.account_id}:role/${var.instance_role_name}",
      "arn:${data.aws_partition.current.partition}:iam::${var.account_id}:instance-profile/${var.instance_profile_name}",
    ]
  }

  statement {
    sid       = "CreateTaggedGitHubOIDCProvider"
    actions   = ["iam:CreateOpenIDConnectProvider"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/Project"
      values   = ["battlevive-bot"]
    }
  }

  statement {
    sid = "ManageGitHubOIDCProvider"
    actions = [
      "iam:DeleteOpenIDConnectProvider",
      "iam:UpdateOpenIDConnectProviderThumbprint", "iam:AddClientIDToOpenIDConnectProvider",
      "iam:RemoveClientIDFromOpenIDConnectProvider", "iam:TagOpenIDConnectProvider",
      "iam:UntagOpenIDConnectProvider",
    ]
    resources = ["arn:${data.aws_partition.current.partition}:iam::${var.account_id}:oidc-provider/token.actions.githubusercontent.com"]
  }

  statement {
    sid = "ManageOperationsBucket"
    actions = [
      "s3:CreateBucket", "s3:DeleteBucket", "s3:Get*", "s3:ListBucket",
      "s3:PutBucketPolicy", "s3:DeleteBucketPolicy", "s3:PutBucketPublicAccessBlock",
      "s3:PutBucketOwnershipControls", "s3:PutBucketVersioning", "s3:PutEncryptionConfiguration",
      "s3:PutLifecycleConfiguration", "s3:PutBucketTagging", "s3:DeletePublicAccessBlock",
      "s3:DeleteBucketOwnershipControls", "s3:DeleteBucketEncryption",
      "s3:DeleteBucketLifecycle", "s3:DeleteBucketTagging",
    ]
    resources = [
      aws_s3_bucket.operations.arn,
    ]
  }

  statement {
    sid = "ManageProjectLogs"
    actions = [
      "logs:CreateLogGroup", "logs:DeleteLogGroup", "logs:PutRetentionPolicy",
      "logs:DeleteRetentionPolicy", "logs:TagResource", "logs:UntagResource",
      "logs:PutMetricFilter", "logs:DeleteMetricFilter",
    ]
    resources = ["arn:${data.aws_partition.current.partition}:logs:${var.aws_region}:${var.account_id}:log-group:/battlevive/production/*"]
  }

  statement {
    sid = "ManageProjectSSMNamedResources"
    actions = [
      "ssm:CreateDocument", "ssm:UpdateDocument", "ssm:UpdateDocumentDefaultVersion",
      "ssm:DeleteDocument", "ssm:AddTagsToResource", "ssm:RemoveTagsFromResource",
      "ssm:PutParameter", "ssm:DeleteParameter", "ssm:GetParameter", "ssm:GetParameters",
    ]
    resources = [
      "arn:${data.aws_partition.current.partition}:ssm:${var.aws_region}:${var.account_id}:document/${local.name}-*",
      "arn:${data.aws_partition.current.partition}:ssm:${var.aws_region}:${var.account_id}:document/battlevive-production-shell",
      "arn:${data.aws_partition.current.partition}:ssm:${var.aws_region}:${var.account_id}:parameter${local.parameter_root}/config/*",
      "arn:${data.aws_partition.current.partition}:ssm:${var.aws_region}:${var.account_id}:parameter${local.parameter_root}/deployment/*",
    ]
  }

  statement {
    sid = "ManageBootstrapTokenParameters"
    actions = [
      "ssm:GetParameter", "ssm:PutParameter",
      "ssm:AddTagsToResource", "ssm:RemoveTagsFromResource",
    ]
    resources = [
      "arn:${data.aws_partition.current.partition}:ssm:${var.aws_region}:${var.account_id}:parameter${local.secret_parameter_names.bootstrap_jwt}",
      "arn:${data.aws_partition.current.partition}:ssm:${var.aws_region}:${var.account_id}:parameter${local.secret_parameter_names.bootstrap_refresh_token}",
    ]
  }

  statement {
    sid       = "CreateTaggedSSMOperations"
    actions   = ["ssm:CreateAssociation", "ssm:CreateMaintenanceWindow"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/Project"
      values   = ["battlevive-bot"]
    }
  }

  statement {
    sid = "ManageTaggedSSMOperations"
    actions = [
      "ssm:UpdateAssociation", "ssm:DeleteAssociation",
      "ssm:RegisterTargetWithMaintenanceWindow", "ssm:DeregisterTargetFromMaintenanceWindow",
      "ssm:RegisterTaskWithMaintenanceWindow", "ssm:DeregisterTaskFromMaintenanceWindow",
      "ssm:UpdateMaintenanceWindow", "ssm:DeleteMaintenanceWindow",
    ]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/Project"
      values   = ["battlevive-bot"]
    }
  }

  statement {
    sid       = "CreateTaggedAlarms"
    actions   = ["cloudwatch:PutMetricAlarm"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/Project"
      values   = ["battlevive-bot"]
    }
  }

  statement {
    sid       = "ManageTaggedAlarms"
    actions   = ["cloudwatch:PutMetricAlarm", "cloudwatch:DeleteAlarms"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/Project"
      values   = ["battlevive-bot"]
    }
  }

  statement {
    sid = "ManageNamedDashboard"
    actions = [
      "cloudwatch:PutDashboard", "cloudwatch:DeleteDashboards",
    ]
    resources = ["arn:${data.aws_partition.current.partition}:cloudwatch::${var.account_id}:dashboard/${local.name}"]
  }

  statement {
    sid = "ManageProjectSNS"
    actions = [
      "sns:CreateTopic", "sns:DeleteTopic", "sns:SetTopicAttributes", "sns:TagResource",
      "sns:UntagResource", "sns:Subscribe", "sns:Unsubscribe",
    ]
    resources = [aws_sns_topic.alerts.arn]
  }

  statement {
    sid       = "CreateTaggedKMSKey"
    actions   = ["kms:CreateKey"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/Project"
      values   = ["battlevive-bot"]
    }
  }

  statement {
    sid = "ManageProjectKMSKey"
    actions = [
      "kms:CreateAlias", "kms:DescribeKey", "kms:DisableKeyRotation", "kms:EnableKeyRotation",
      "kms:GetKeyPolicy", "kms:GetKeyRotationStatus", "kms:ListResourceTags", "kms:PutKeyPolicy",
      "kms:ScheduleKeyDeletion", "kms:TagResource", "kms:UntagResource",
    ]
    resources = [aws_kms_key.alerts.arn]
  }

  statement {
    sid = "ManageProjectKMSAlias"
    actions = [
      "kms:CreateAlias", "kms:DeleteAlias", "kms:UpdateAlias",
    ]
    resources = ["arn:${data.aws_partition.current.partition}:kms:${var.aws_region}:${var.account_id}:alias/${local.name}-alerts"]
  }

  statement {
    sid = "ManageAdoptedCompute"
    actions = [
      "ec2:CreateTags", "ec2:DeleteTags", "ec2:ModifyInstanceAttribute",
      "ec2:AssociateIamInstanceProfile", "ec2:DisassociateIamInstanceProfile",
      "ec2:ReplaceIamInstanceProfileAssociation", "ec2:StartInstances", "ec2:StopInstances",
      "ec2:ModifyVolume", "ec2:RevokeSecurityGroupIngress", "ec2:AuthorizeSecurityGroupEgress",
      "ec2:RevokeSecurityGroupEgress", "ec2:ModifySecurityGroupRules",
    ]
    resources = [
      aws_instance.bot.arn,
      aws_security_group.bot.arn,
      "arn:${data.aws_partition.current.partition}:ec2:${var.aws_region}:${var.account_id}:volume/${aws_instance.bot.root_block_device[0].volume_id}",
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
    data.aws_iam_policy_document.state_apply_access.json,
  ]
}

data "aws_iam_policy_document" "deploy" {
  statement {
    actions   = ["ssm:GetParameter"]
    resources = [aws_ssm_parameter.deployment_state.arn]
  }
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

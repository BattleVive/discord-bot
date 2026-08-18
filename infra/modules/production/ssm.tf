resource "aws_ssm_parameter" "cloudwatch_agent_config" {
  name  = "${local.parameter_root}/config/cloudwatch-agent"
  type  = "String"
  tier  = "Standard"
  value = file("${path.module}/../../host/cloudwatch-agent.json")
}

resource "aws_ssm_association" "install_cloudwatch_agent" {
  name = "AWS-ConfigureAWSPackage"
  parameters = {
    action           = "Install"
    installationType = "Uninstall and reinstall"
    name             = "AmazonCloudWatchAgent"
  }
  targets {
    key    = "InstanceIds"
    values = [aws_instance.bot.id]
  }
  tags = local.common_tags
}

resource "aws_ssm_association" "configure_cloudwatch_agent" {
  name = "AmazonCloudWatch-ManageAgent"
  parameters = {
    action                        = "configure"
    mode                          = "ec2"
    optionalConfigurationLocation = aws_ssm_parameter.cloudwatch_agent_config.name
    optionalConfigurationSource   = "ssm"
    optionalRestart               = "yes"
  }
  targets {
    key    = "InstanceIds"
    values = [aws_instance.bot.id]
  }
  depends_on = [aws_ssm_association.install_cloudwatch_agent]
  tags       = local.common_tags
}

resource "aws_ssm_document" "session" {
  name            = "battlevive-production-shell"
  document_type   = "Session"
  document_format = "JSON"
  content = jsonencode({
    schemaVersion = "1.0"
    description   = "Battlevive production Session Manager shell with CloudWatch logging"
    sessionType   = "Standard_Stream"
    inputs = {
      cloudWatchLogGroupName      = aws_cloudwatch_log_group.production["session"].name
      cloudWatchEncryptionEnabled = false
      cloudWatchStreamingEnabled  = true
      idleSessionTimeout          = "20"
      runAsEnabled                = false
      shellProfile = {
        linux = "cd /opt/battlevive 2>/dev/null || cd /"
      }
    }
  })
  tags = local.common_tags
}

resource "aws_ssm_document" "deploy" {
  name            = "${local.name}-deploy"
  document_type   = "Command"
  document_format = "YAML"
  content = yamlencode({
    schemaVersion = "2.2"
    description   = "Deploy an immutable Battlevive release bundle"
    parameters = {
      environment      = { type = "String", allowedValues = ["production"], interpolationType = "ENV_VAR" }
      version          = { type = "String", allowedPattern = "^[0-9]+\\.[0-9]+\\.[0-9]+$", interpolationType = "ENV_VAR" }
      imageDigest      = { type = "String", allowedPattern = "^sha256:[a-f0-9]{64}$", interpolationType = "ENV_VAR" }
      bundleKey        = { type = "String", allowedPattern = "^releases/[A-Za-z0-9._/-]+$", interpolationType = "ENV_VAR" }
      bundleChecksum   = { type = "String", allowedPattern = "^[a-f0-9]{64}$", interpolationType = "ENV_VAR" }
      targetSelector   = { type = "String", interpolationType = "ENV_VAR" }
      operationsBucket = { type = "String", default = aws_s3_bucket.operations.id, allowedPattern = "^[a-z0-9.-]+$", interpolationType = "ENV_VAR" }
      awsRegion        = { type = "String", default = var.aws_region, allowedValues = ["eu-north-1"], interpolationType = "ENV_VAR" }
    }
    mainSteps = [{
      action = "aws:runShellScript"
      name   = "deploy"
      precondition = {
        StringEquals = ["platformType", "Linux"]
      }
      inputs = {
        timeoutSeconds = "330"
        runCommand = [
          "set -eu",
          "export OPERATIONS_BUCKET=\"$SSM_operationsBucket\" AWS_REGION=\"$SSM_awsRegion\" BATTLEVIVE_OPERATIONS_LOCK=/run/lock/battlevive-operations.lock",
          "timeout 295 /usr/local/libexec/battlevive/deploy --environment \"$SSM_environment\" --version \"$SSM_version\" --image-digest \"$SSM_imageDigest\" --bundle-key \"$SSM_bundleKey\" --bundle-checksum \"$SSM_bundleChecksum\" --target-selector \"$SSM_targetSelector\"",
        ]
      }
    }]
  })
  tags = local.common_tags
}

resource "aws_ssm_document" "host_bootstrap" {
  name            = "${local.name}-host-bootstrap"
  document_type   = "Command"
  document_format = "YAML"
  content = yamlencode({
    schemaVersion = "2.2"
    description   = "Install checked-in Battlevive host units after the release bundle is present"
    mainSteps = [{
      action = "aws:runShellScript"
      name   = "installHostAssets"
      inputs = {
        timeoutSeconds = "120"
        runCommand = [
          "set -eu",
          "if test -x /opt/battlevive/current/install.sh; then BATTLEVIVE_BUNDLE_ROOT=/opt/battlevive/current /opt/battlevive/current/install.sh; fi",
        ]
      }
    }]
  })
  tags = local.common_tags
}

resource "aws_ssm_association" "host_bootstrap" {
  name             = aws_ssm_document.host_bootstrap.name
  association_name = "${local.name}-host-bootstrap"
  targets {
    key    = "InstanceIds"
    values = [aws_instance.bot.id]
  }
  tags = local.common_tags
}

resource "aws_ssm_maintenance_window" "monthly_patch" {
  name                       = "${local.name}-monthly-patch"
  schedule                   = "cron(0 3 ? * SUN#4 *)"
  schedule_timezone          = "Europe/Warsaw"
  duration                   = 3
  cutoff                     = 1
  allow_unassociated_targets = false
  tags                       = local.common_tags
}

resource "aws_ssm_maintenance_window_target" "instance" {
  window_id     = aws_ssm_maintenance_window.monthly_patch.id
  name          = "${local.name}-instance"
  description   = "Tagged production instance"
  resource_type = "INSTANCE"
  targets {
    key    = "InstanceIds"
    values = [aws_instance.bot.id]
  }
}

resource "aws_ssm_maintenance_window_task" "patch" {
  window_id        = aws_ssm_maintenance_window.monthly_patch.id
  name             = "${local.name}-install-security-patches"
  task_type        = "RUN_COMMAND"
  task_arn         = "AWS-RunPatchBaseline"
  priority         = 1
  max_concurrency  = "1"
  max_errors       = "1"
  service_role_arn = aws_iam_role.maintenance_window.arn

  targets {
    key    = "WindowTargetIds"
    values = [aws_ssm_maintenance_window_target.instance.id]
  }

  task_invocation_parameters {
    run_command_parameters {
      timeout_seconds = 7200
      parameter {
        name   = "Operation"
        values = ["Install"]
      }
      parameter {
        name   = "RebootOption"
        values = ["RebootIfNeeded"]
      }
    }
  }
}

data "aws_iam_policy_document" "maintenance_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ssm.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "maintenance_window" {
  name               = "${local.name}-maintenance-window"
  assume_role_policy = data.aws_iam_policy_document.maintenance_assume.json
  tags               = local.common_tags
}

resource "aws_iam_role_policy" "maintenance_window" {
  name = "run-patch-on-production-instance"
  role = aws_iam_role.maintenance_window.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["ssm:SendCommand"]
        Resource = [aws_instance.bot.arn, "arn:${data.aws_partition.current.partition}:ssm:${var.aws_region}::document/AWS-RunPatchBaseline"]
      },
      {
        Effect   = "Allow"
        Action   = ["ssm:GetCommandInvocation", "ssm:ListCommands", "ssm:ListCommandInvocations"]
        Resource = ["*"]
      },
    ]
  })
}

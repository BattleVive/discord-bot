resource "aws_cloudwatch_log_group" "production" {
  for_each = local.log_groups

  name              = each.value
  retention_in_days = 90
  skip_destroy      = true

  tags = local.common_tags
}

resource "aws_sns_topic" "alerts" {
  name              = "${local.name}-alerts"
  kms_master_key_id = aws_kms_key.alerts.arn
  tags              = local.common_tags
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

resource "aws_cloudwatch_log_metric_filter" "critical_application" {
  name           = "${local.name}-critical-application"
  pattern        = "?CRITICAL ?FATAL"
  log_group_name = aws_cloudwatch_log_group.production["application"].name

  metric_transformation {
    name      = "CriticalApplicationLogs"
    namespace = "Battlevive/Production"
    value     = "1"
  }
}

resource "aws_cloudwatch_log_metric_filter" "oom" {
  name           = "${local.name}-oom"
  pattern        = "?oom-kill ?OutOfMemory ?\"Out of memory\""
  log_group_name = aws_cloudwatch_log_group.production["system"].name
  metric_transformation {
    name      = "OOMEvents"
    namespace = "Battlevive/Production"
    value     = "1"
  }
}

locals {
  alarms = {
    instance_status = {
      namespace           = "AWS/EC2"
      metric_name         = "StatusCheckFailed"
      statistic           = "Maximum"
      period              = 60
      evaluation_periods  = 2
      threshold           = 0
      comparison_operator = "GreaterThanThreshold"
      treat_missing_data  = "missing"
      dimensions          = { InstanceId = aws_instance.bot.id }
    }
    memory = {
      namespace           = "CWAgent"
      metric_name         = "mem_used_percent"
      statistic           = "Maximum"
      period              = 300
      evaluation_periods  = 3
      threshold           = 80
      comparison_operator = "GreaterThanThreshold"
      treat_missing_data  = "breaching"
      dimensions          = { InstanceId = aws_instance.bot.id }
    }
    disk = {
      namespace           = "CWAgent"
      metric_name         = "disk_used_percent"
      statistic           = "Maximum"
      period              = 300
      evaluation_periods  = 3
      threshold           = 80
      comparison_operator = "GreaterThanThreshold"
      treat_missing_data  = "breaching"
      dimensions          = { InstanceId = aws_instance.bot.id }
    }
    swap = {
      namespace           = "CWAgent"
      metric_name         = "swap_used_percent"
      statistic           = "Maximum"
      period              = 300
      evaluation_periods  = 1
      threshold           = 0
      comparison_operator = "GreaterThanThreshold"
      treat_missing_data  = "notBreaching"
      dimensions          = { InstanceId = aws_instance.bot.id }
    }
    cpu_credits = {
      namespace           = "AWS/EC2"
      metric_name         = "CPUCreditBalance"
      statistic           = "Minimum"
      period              = 300
      evaluation_periods  = 3
      threshold           = 24
      comparison_operator = "LessThanThreshold"
      treat_missing_data  = "breaching"
      dimensions          = { InstanceId = aws_instance.bot.id }
    }
    cpu_surplus = {
      namespace           = "AWS/EC2"
      metric_name         = "CPUSurplusCreditsCharged"
      statistic           = "Sum"
      period              = 3600
      evaluation_periods  = 1
      threshold           = 0
      comparison_operator = "GreaterThanThreshold"
      treat_missing_data  = "notBreaching"
      dimensions          = { InstanceId = aws_instance.bot.id }
    }
    bot_health = {
      namespace           = "Battlevive/Production"
      metric_name         = "BotHealthy"
      statistic           = "Minimum"
      period              = 60
      evaluation_periods  = 3
      threshold           = 1
      comparison_operator = "LessThanThreshold"
      treat_missing_data  = "breaching"
      dimensions          = {}
    }
    host_telemetry = {
      namespace           = "Battlevive/Production"
      metric_name         = "HostTelemetryHealthy"
      statistic           = "Minimum"
      period              = 60
      evaluation_periods  = 3
      threshold           = 1
      comparison_operator = "LessThanThreshold"
      treat_missing_data  = "breaching"
      dimensions          = {}
    }
    backup_failure = {
      namespace           = "Battlevive/Production"
      metric_name         = "BackupSuccess"
      statistic           = "Minimum"
      period              = 300
      evaluation_periods  = 1
      threshold           = 1
      comparison_operator = "LessThanThreshold"
      treat_missing_data  = "notBreaching"
      dimensions          = {}
    }
    backup_stale = {
      namespace           = "Battlevive/Production"
      metric_name         = "BackupFresh"
      statistic           = "Minimum"
      period              = 86400
      evaluation_periods  = 1
      threshold           = 1
      comparison_operator = "LessThanThreshold"
      treat_missing_data  = "breaching"
      dimensions          = {}
    }
    restore_drill_failure = {
      namespace           = "Battlevive/Production"
      metric_name         = "RestoreDrillSuccess"
      statistic           = "Minimum"
      period              = 300
      evaluation_periods  = 1
      threshold           = 1
      comparison_operator = "LessThanThreshold"
      treat_missing_data  = "notBreaching"
      dimensions          = {}
    }
    restore_drill_stale = {
      namespace           = "Battlevive/Production"
      metric_name         = "RestoreDrillFresh"
      statistic           = "Minimum"
      period              = 86400
      evaluation_periods  = 1
      threshold           = 1
      comparison_operator = "LessThanThreshold"
      treat_missing_data  = "breaching"
      dimensions          = {}
    }
    deployment = {
      namespace           = "Battlevive/Production"
      metric_name         = "DeploymentSuccess"
      statistic           = "Minimum"
      period              = 300
      evaluation_periods  = 1
      threshold           = 1
      comparison_operator = "LessThanThreshold"
      treat_missing_data  = "notBreaching"
      dimensions          = {}
    }
    deployment_failure = {
      namespace           = "Battlevive/Production"
      metric_name         = "DeploymentFailure"
      statistic           = "Sum"
      period              = 300
      evaluation_periods  = 1
      threshold           = 0
      comparison_operator = "GreaterThanThreshold"
      treat_missing_data  = "notBreaching"
      dimensions          = {}
    }
    critical_logs = {
      namespace           = "Battlevive/Production"
      metric_name         = "CriticalApplicationLogs"
      statistic           = "Sum"
      period              = 300
      evaluation_periods  = 1
      threshold           = 0
      comparison_operator = "GreaterThanThreshold"
      treat_missing_data  = "notBreaching"
      dimensions          = {}
    }
    oom = {
      namespace           = "Battlevive/Production"
      metric_name         = "OOMEvents"
      statistic           = "Sum"
      period              = 300
      evaluation_periods  = 1
      threshold           = 0
      comparison_operator = "GreaterThanThreshold"
      treat_missing_data  = "notBreaching"
      dimensions          = {}
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "production" {
  for_each = local.alarms

  alarm_name          = "${local.name}-${replace(each.key, "_", "-")}"
  alarm_description   = "Battlevive production ${replace(each.key, "_", " ")} alarm"
  namespace           = each.value.namespace
  metric_name         = each.value.metric_name
  statistic           = each.value.statistic
  period              = each.value.period
  evaluation_periods  = each.value.evaluation_periods
  threshold           = each.value.threshold
  comparison_operator = each.value.comparison_operator
  treat_missing_data  = each.value.treat_missing_data
  dimensions          = each.value.dimensions
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]

  tags = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "inode_usage" {
  alarm_name          = "${local.name}-inode-usage"
  alarm_description   = "Root filesystem inode use is above 80 percent"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  threshold           = 80
  treat_missing_data  = "breaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]

  metric_query {
    id          = "inode_percent"
    expression  = "100 * used / total"
    label       = "Inodes used percent"
    return_data = true
  }
  metric_query {
    id          = "used"
    return_data = false
    metric {
      namespace   = "CWAgent"
      metric_name = "disk_inodes_used"
      period      = 300
      stat        = "Maximum"
      dimensions  = { InstanceId = aws_instance.bot.id }
    }
  }
  metric_query {
    id          = "total"
    return_data = false
    metric {
      namespace   = "CWAgent"
      metric_name = "disk_inodes_total"
      period      = 300
      stat        = "Maximum"
      dimensions  = { InstanceId = aws_instance.bot.id }
    }
  }

  tags = local.common_tags
}

resource "aws_cloudwatch_dashboard" "production" {
  dashboard_name = local.name
  dashboard_body = jsonencode({
    widgets = [
      {
        type = "metric", x = 0, y = 0, width = 12, height = 6,
        properties = {
          title = "Instance resources", region = var.aws_region, view = "timeSeries",
          metrics = [
            ["AWS/EC2", "CPUUtilization", "InstanceId", aws_instance.bot.id],
            ["CWAgent", "mem_used_percent", "InstanceId", aws_instance.bot.id],
            ["CWAgent", "swap_used_percent", "InstanceId", aws_instance.bot.id],
          ]
        }
      },
      {
        type = "metric", x = 12, y = 0, width = 12, height = 6,
        properties = {
          title = "Service and operations", region = var.aws_region, view = "timeSeries",
          metrics = [
            ["Battlevive/Production", "BotHealthy"],
            [".", "BackupSuccess"],
            [".", "RestoreDrillSuccess"],
            [".", "DeploymentSuccess"],
          ]
        }
      },
      {
        type = "log", x = 0, y = 6, width = 24, height = 6,
        properties = {
          title = "Application errors", region = var.aws_region,
          query = "SOURCE '${aws_cloudwatch_log_group.production["application"].name}' | fields @timestamp, @message | filter @message like /ERROR|CRITICAL|FATAL/ | sort @timestamp desc | limit 50"
        }
      },
    ]
  })
}

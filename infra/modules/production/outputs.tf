output "github_plan_role_arn" { value = aws_iam_role.github_plan.arn }
output "github_apply_role_arn" { value = aws_iam_role.github_apply.arn }
output "github_deploy_role_arn" { value = aws_iam_role.github_deploy.arn }
output "operations_bucket" { value = aws_s3_bucket.operations.id }
output "sns_topic_arn" { value = aws_sns_topic.alerts.arn }
output "runtime_parameter_names" {
  value = merge(local.secret_parameter_names, {
    deployed_version  = aws_ssm_parameter.deployed_version.name
    deployed_digest   = aws_ssm_parameter.deployed_digest.name
    operations_bucket = aws_ssm_parameter.operations_bucket.name
  })
}

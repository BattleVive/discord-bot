output "github_role_arns" {
  value = {
    plan   = module.production.github_plan_role_arn
    apply  = module.production.github_apply_role_arn
    deploy = module.production.github_deploy_role_arn
  }
}

output "operations_bucket" {
  value = module.production.operations_bucket
}

output "sns_topic_arn" {
  value = module.production.sns_topic_arn
}

output "runtime_parameter_names" {
  description = "Names only. Secret values are entered with infra/tools/migrate-secrets.sh."
  value       = module.production.runtime_parameter_names
}

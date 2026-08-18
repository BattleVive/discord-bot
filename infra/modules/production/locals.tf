locals {
  name              = "battlevive-production"
  parameter_root    = "/battlevive/production"
  operations_bucket = "battlevive-bot-operations-${var.account_id}-${var.operations_bucket_suffix}"
  log_groups = {
    application = "/battlevive/production/application"
    system      = "/battlevive/production/system"
    session     = "/battlevive/production/session-manager"
  }
  common_tags = {
    Project     = "battlevive-bot"
    Environment = "production"
  }
  oidc_provider_arn = coalesce(var.github_oidc_provider_arn, try(aws_iam_openid_connect_provider.github[0].arn, null))
  oidc_hostpath     = replace(local.oidc_provider_arn, "arn:aws:iam::${var.account_id}:oidc-provider/", "")
  secret_parameter_names = {
    database_url            = "${local.parameter_root}/secrets/database-url"
    discord_token           = "${local.parameter_root}/secrets/discord-token"
    supabase_api_key        = "${local.parameter_root}/secrets/supabase-api-key"
    bootstrap_jwt           = "${local.parameter_root}/secrets/bootstrap-jwt"
    bootstrap_refresh_token = "${local.parameter_root}/secrets/bootstrap-refresh-token"
    postgres_password       = "${local.parameter_root}/secrets/postgres-password"
    rotating_tokens         = "${local.parameter_root}/tokens"
  }
}

resource "aws_ssm_parameter" "discord_token" {
  name  = "${local.parameter_root}/secrets/discord-token"
  type  = "SecureString"
  value = var.discord_token
}

resource "aws_ssm_parameter" "battlevive_api_key" {
  name  = "${local.parameter_root}/secrets/battlevive-api-key"
  type  = "SecureString"
  value = var.battlevive_api_key
}

resource "aws_ssm_parameter" "database_secret_arn" {
  name  = "${local.parameter_root}/config/database-secret-arn"
  type  = "String"
  value = aws_db_instance.postgres.master_user_secret[0].secret_arn
}

resource "aws_ssm_parameter" "database_host" {
  name  = "${local.parameter_root}/config/database-host"
  type  = "String"
  value = aws_db_instance.postgres.address
}

module "slot" {
  source                  = "../../modules/slot"
  aws_region              = var.aws_region
  discord_token           = var.discord_token
  battlevive_api_key      = var.battlevive_api_key
  operations_bucket       = var.operations_bucket
  rds_snapshot_identifier = var.rds_snapshot_identifier
  slot                    = "green"
}

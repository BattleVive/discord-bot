module "slot" {
  source                  = "../../modules/slot"
  aws_region              = var.aws_region
  discord_token           = var.discord_token
  network_cidr            = "10.81.0.0/16"
  operations_bucket       = var.operations_bucket
  rds_snapshot_identifier = var.rds_snapshot_identifier
  slot                    = "green"
}

module "staging" {
  source            = "../modules/slot"
  aws_region        = var.aws_region
  discord_token     = var.discord_token
  network_cidr      = "10.240.0.0/16"
  operations_bucket = var.operations_bucket
  name_prefix       = "battlevive-v2-e2e"
  parameter_root    = "/battlevive-v2-e2e"
  project           = "battlevive-v2-e2e"
  slot              = "blue"
}

resource "aws_db_snapshot" "staging" {
  db_instance_identifier = module.staging.rds_identifier
  db_snapshot_identifier = "battlevive-v2-e2e-staging-snapshot"
}

module "production" {
  source                  = "../modules/slot"
  aws_region              = var.aws_region
  discord_token           = var.discord_token
  network_cidr            = "10.241.0.0/16"
  operations_bucket       = var.operations_bucket
  name_prefix             = "battlevive-v2-e2e"
  parameter_root          = "/battlevive-v2-e2e"
  project                 = "battlevive-v2-e2e"
  rds_snapshot_identifier = aws_db_snapshot.staging.id
  slot                    = "green"
}

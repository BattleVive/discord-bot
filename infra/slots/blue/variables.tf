variable "aws_region" {
  type    = string
  default = "eu-north-1"
}
variable "discord_token" {
  type      = string
  sensitive = true
}
variable "operations_bucket" { type = string }
variable "rds_snapshot_identifier" {
  type    = string
  default = null
}

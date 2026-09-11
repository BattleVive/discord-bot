variable "aws_region" {
  type    = string
  default = "eu-north-1"
}
variable "operations_bucket" { type = string }
variable "discord_token" {
  type      = string
  sensitive = true
}

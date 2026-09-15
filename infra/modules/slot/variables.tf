variable "aws_region" { type = string }
variable "discord_token" {
  type      = string
  sensitive = true
}
variable "battlevive_api_key" {
  type      = string
  sensitive = true
}
variable "operations_bucket" { type = string }
variable "name_prefix" {
  type        = string
  default     = "battlevive"
  description = "Resource-name prefix. Keep the production default; give disposable environments their own prefix."
}
variable "parameter_root" {
  type        = string
  default     = "/battlevive"
  description = "Root SSM path, without the slot component."
  validation {
    condition     = startswith(var.parameter_root, "/") && !endswith(var.parameter_root, "/")
    error_message = "parameter_root must start with / and must not end with /."
  }
}
variable "project" {
  type    = string
  default = "battlevive-bot"
}
variable "rds_snapshot_identifier" {
  type    = string
  default = null
}
variable "slot" {
  type = string

  validation {
    condition     = contains(["blue", "green"], var.slot)
    error_message = "slot must be blue or green."
  }
}

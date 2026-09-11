variable "aws_region" { type = string }
variable "discord_token" {
  type      = string
  sensitive = true
}
variable "operations_bucket" { type = string }
variable "network_cidr" {
  type        = string
  description = "Non-overlapping /16 CIDR owned exclusively by this slot."
  validation {
    condition     = can(cidrhost(var.network_cidr, 0)) && split("/", var.network_cidr)[1] == "16"
    error_message = "network_cidr must be a valid /16 CIDR."
  }
}
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
variable "slot" { type = string }

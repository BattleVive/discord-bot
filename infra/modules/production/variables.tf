terraform {
  required_providers {
    time = {
      source = "hashicorp/time"
    }
  }
}

variable "account_id" { type = string }
variable "alert_email" { type = string }
variable "ami_id" { type = string }
variable "aws_region" { type = string }
variable "availability_zone" { type = string }
variable "github_oidc_provider_arn" {
  type    = string
  default = null
}
variable "github_oidc_repository" { type = string }
variable "instance_profile_name" { type = string }
variable "instance_role_name" { type = string }
variable "instance_type" { type = string }
variable "operations_bucket_suffix" { type = string }
variable "root_device_name" { type = string }
variable "root_volume_encrypted" { type = bool }
variable "root_volume_size" { type = number }
variable "root_volume_type" { type = string }
variable "security_group_name" { type = string }
variable "security_group_description" { type = string }
variable "state_bucket_name" { type = string }
variable "bootstrap_jwt" {
  type      = string
  sensitive = true
  ephemeral = true
}
variable "bootstrap_refresh_token" {
  type      = string
  sensitive = true
  ephemeral = true
}
variable "bootstrap_token_generation" { type = number }
variable "subnet_id" { type = string }
variable "temporary_ssh_ingress_cidr" {
  description = "Temporary existing SSH CIDR retained only until a fresh SSM session succeeds; null is the required steady state."
  type        = string
  default     = null

  validation {
    condition     = var.temporary_ssh_ingress_cidr == null || can(cidrnetmask(var.temporary_ssh_ingress_cidr))
    error_message = "temporary_ssh_ingress_cidr must be null or a valid IPv4 CIDR."
  }
}
variable "vpc_id" { type = string }

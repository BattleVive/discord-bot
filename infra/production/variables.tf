variable "aws_region" {
  type    = string
  default = "eu-north-1"

  validation {
    condition     = var.aws_region == "eu-north-1"
    error_message = "Production must remain in eu-north-1."
  }
}

variable "alert_email" {
  description = "SNS subscription endpoint. Supply interactively or in protected CI; it is not a secret."
  type        = string
}

variable "github_repository" {
  type    = string
  default = "voxix-dev/battlevive-bot"
}

variable "github_oidc_provider_arn" {
  description = "Existing account-wide GitHub OIDC provider ARN, or null to create it."
  type        = string
  default     = null
}

variable "instance" {
  description = "Sanitized existing EC2 inventory. Values must exactly match the adopted instance before apply."
  type = object({
    id                         = string
    ami_id                     = string
    instance_type              = string
    availability_zone          = string
    vpc_id                     = string
    subnet_id                  = string
    security_group_id          = string
    security_group_name        = string
    security_group_description = string
    iam_role_name              = string
    instance_profile_name      = string
    root_device_name           = string
    root_volume_size           = number
    root_volume_type           = string
    root_volume_encrypted      = bool
  })

  validation {
    condition     = startswith(var.instance.availability_zone, "eu-north-1")
    error_message = "The adopted instance must be in eu-north-1."
  }
}

variable "operations_bucket_suffix" {
  description = "Stable lowercase suffix chosen during bootstrap; do not use a secret."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9]{8,32}$", var.operations_bucket_suffix))
    error_message = "Use 8-32 lowercase letters or digits."
  }
}

variable "state_bucket_name" {
  description = "Exact private bucket name output by infra/bootstrap."
  type        = string
}

variable "create_imports" {
  description = "Set true only for the one-time manual adoption plan/apply."
  type        = bool
  default     = false
}

variable "temporary_ssh_ingress_cidr" {
  description = "Temporary adoption-only SSH CIDR. Set back to null immediately after proving a new SSM session."
  type        = string
  default     = null

  validation {
    condition     = var.temporary_ssh_ingress_cidr == null || can(cidrnetmask(var.temporary_ssh_ingress_cidr))
    error_message = "temporary_ssh_ingress_cidr must be null or a valid IPv4 CIDR."
  }
}

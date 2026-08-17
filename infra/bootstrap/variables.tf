variable "aws_region" {
  description = "AWS region containing production."
  type        = string
  default     = "eu-north-1"

  validation {
    condition     = var.aws_region == "eu-north-1"
    error_message = "Production state must remain in eu-north-1."
  }
}

variable "aws_profile" {
  description = "Local IAM Identity Center profile used only for manual bootstrap."
  type        = string
  default     = "battlevive-prod"
}

variable "account_id" {
  description = "Twelve-digit AWS account ID; used in the globally unique bucket name."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "account_id must be a twelve-digit AWS account ID."
  }
}

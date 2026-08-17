variable "account_id" { type = string }
variable "alert_email" { type = string }
variable "ami_id" { type = string }
variable "aws_region" { type = string }
variable "availability_zone" { type = string }
variable "github_oidc_provider_arn" {
  type    = string
  default = null
}
variable "github_repository" { type = string }
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
variable "subnet_id" { type = string }
variable "vpc_id" { type = string }

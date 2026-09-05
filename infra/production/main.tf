data "aws_caller_identity" "current" {}

data "aws_vpc" "production" {
  id = var.instance.vpc_id
}

data "aws_subnet" "production" {
  id = var.instance.subnet_id
}

module "production" {
  source = "../modules/production"

  account_id                 = data.aws_caller_identity.current.account_id
  alert_email                = var.alert_email
  ami_id                     = var.instance.ami_id
  aws_region                 = var.aws_region
  availability_zone          = var.instance.availability_zone
  bootstrap_jwt              = var.bootstrap_jwt
  bootstrap_refresh_token    = var.bootstrap_refresh_token
  bootstrap_token_generation = var.bootstrap_token_generation
  github_oidc_provider_arn   = var.github_oidc_provider_arn
  github_oidc_subjects       = var.github_oidc_subjects
  instance_profile_name      = var.instance.instance_profile_name
  instance_type              = var.instance.instance_type
  instance_role_name         = var.instance.iam_role_name
  operations_bucket_suffix   = var.operations_bucket_suffix
  root_device_name           = var.instance.root_device_name
  root_volume_encrypted      = var.instance.root_volume_encrypted
  root_volume_size           = var.instance.root_volume_size
  root_volume_type           = var.instance.root_volume_type
  security_group_name        = var.instance.security_group_name
  security_group_description = var.instance.security_group_description
  state_bucket_name          = var.state_bucket_name
  subnet_id                  = data.aws_subnet.production.id
  temporary_ssh_ingress_cidr = var.temporary_ssh_ingress_cidr
  vpc_id                     = data.aws_vpc.production.id
}

import {
  for_each = var.create_imports ? { instance = var.instance.id } : {}
  to       = module.production.aws_instance.bot
  id       = each.value
}

import {
  for_each = var.create_imports ? { security_group = var.instance.security_group_id } : {}
  to       = module.production.aws_security_group.bot
  id       = each.value
}

import {
  for_each = var.create_imports ? { role = var.instance.iam_role_name } : {}
  to       = module.production.aws_iam_role.instance
  id       = each.value
}

import {
  for_each = var.create_imports ? { profile = var.instance.instance_profile_name } : {}
  to       = module.production.aws_iam_instance_profile.bot
  id       = each.value
}

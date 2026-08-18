mock_provider "aws" {
  mock_data "aws_iam_policy_document" {
    defaults = {
      json = jsonencode({
        Version   = "2012-10-17"
        Statement = []
      })
    }
  }

  mock_data "aws_partition" {
    defaults = {
      partition = "aws"
    }
  }

  mock_resource "aws_iam_role" {
    defaults = {
      arn = "arn:aws:iam::598122632788:role/mock-role"
    }
  }

  mock_resource "aws_sns_topic" {
    defaults = {
      arn = "arn:aws:sns:eu-north-1:598122632788:mock-topic"
    }
  }

  mock_resource "aws_kms_key" {
    defaults = {
      arn    = "arn:aws:kms:eu-north-1:598122632788:key/01234567-89ab-cdef-0123-456789abcdef"
      key_id = "01234567-89ab-cdef-0123-456789abcdef"
    }
  }
}

variables {
  account_id                 = "598122632788"
  alert_email                = "operator@example.invalid"
  ami_id                     = "ami-0123456789abcdef0"
  aws_region                 = "eu-north-1"
  availability_zone          = "eu-north-1c"
  github_repository          = "voxix-dev/battlevive-bot"
  instance_profile_name      = "battlevive-production-instance"
  instance_role_name         = "battlevive-production-instance"
  instance_type              = "t4g.micro"
  operations_bucket_suffix   = "adoptiontest"
  root_device_name           = "/dev/xvda"
  root_volume_encrypted      = false
  root_volume_size           = 8
  root_volume_type           = "gp3"
  security_group_name        = "launch-wizard-1"
  security_group_description = "Existing production security group"
  state_bucket_name          = "battlevive-test-state"
  subnet_id                  = "subnet-0123456789abcdef0"
  vpc_id                     = "vpc-0123456789abcdef0"
}

run "temporary_ssh_is_explicitly_preserved_during_ssm_adoption" {
  command = apply

  variables {
    temporary_ssh_ingress_cidr = "0.0.0.0/0"
  }

  assert {
    condition = anytrue([
      for rule in aws_security_group.bot.ingress :
      rule.protocol == "tcp" && rule.from_port == 22 && rule.to_port == 22 &&
      contains(rule.cidr_blocks, "0.0.0.0/0")
    ])
    error_message = "The first adoption apply must preserve the existing SSH path until SSM is proven."
  }

  assert {
    condition = alltrue([
      for name in keys(yamldecode(aws_ssm_document.deploy.content).parameters) :
      can(regex("^[A-Za-z0-9]+$", name))
    ])
    error_message = "SSM Command document parameter names must be alphanumeric."
  }
}

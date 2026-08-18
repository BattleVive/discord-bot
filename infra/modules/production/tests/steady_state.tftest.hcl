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
  operations_bucket_suffix   = "steadystate"
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

run "steady_state_has_zero_ingress" {
  command = apply

  assert {
    condition     = length(aws_security_group.bot.ingress) == 0
    error_message = "Production steady state must have no security-group ingress."
  }

  assert {
    condition     = aws_instance.bot.metadata_options[0].http_put_response_hop_limit == 2
    error_message = "The Dockerized bot needs IMDSv2 responses to cross the container network hop."
  }
}

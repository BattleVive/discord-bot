resource "aws_security_group" "bot" {
  name        = var.security_group_name
  description = var.security_group_description
  vpc_id      = var.vpc_id

  # Intentionally no ingress blocks.
  egress {
    description = "HTTPS, image registry, Discord, Supabase and AWS APIs"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  lifecycle {
    prevent_destroy = true
  }

  tags = merge(local.common_tags, { Name = "${local.name}-zero-ingress" })
}

resource "aws_instance" "bot" {
  ami                                  = var.ami_id
  availability_zone                    = var.availability_zone
  disable_api_stop                     = false
  disable_api_termination              = true
  iam_instance_profile                 = aws_iam_instance_profile.bot.name
  instance_initiated_shutdown_behavior = "stop"
  instance_type                        = var.instance_type
  monitoring                           = true
  subnet_id                            = var.subnet_id
  vpc_security_group_ids               = [aws_security_group.bot.id]

  metadata_options {
    http_endpoint               = "enabled"
    http_protocol_ipv6          = "disabled"
    http_put_response_hop_limit = 1
    http_tokens                 = "required"
    instance_metadata_tags      = "disabled"
  }

  root_block_device {
    delete_on_termination = false
    encrypted             = var.root_volume_encrypted
    volume_size           = var.root_volume_size
    volume_type           = var.root_volume_type
  }

  lifecycle {
    prevent_destroy = true
  }

  tags = merge(local.common_tags, {
    Name = local.name
  })

  volume_tags = merge(local.common_tags, {
    Name = "${local.name}-root"
  })
}

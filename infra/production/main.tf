data "aws_caller_identity" "current" {}
locals {
  parameter_root = "/battlevive/production"
  bucket         = "battlevive-bot-operations-${data.aws_caller_identity.current.account_id}-${var.operations_bucket_suffix}"
}
resource "aws_s3_bucket" "operations" { bucket = local.bucket }
resource "aws_s3_bucket_public_access_block" "operations" {
  bucket                  = aws_s3_bucket.operations.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_versioning" "operations" {
  bucket = aws_s3_bucket.operations.id
  versioning_configuration { status = "Enabled" }
}
resource "aws_s3_bucket_server_side_encryption_configuration" "operations" {
  bucket = aws_s3_bucket.operations.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}
resource "aws_ssm_parameter" "active_slot" {
  name  = "${local.parameter_root}/control/active-slot"
  type  = "String"
  value = var.active_slot
  lifecycle {
    precondition {
      condition     = var.active_slot != var.release_candidate_slot
      error_message = "active_slot and release_candidate_slot must differ."
    }
  }
}
resource "aws_ssm_parameter" "release_candidate_slot" {
  name  = "${local.parameter_root}/control/release-candidate-slot"
  type  = "String"
  value = var.release_candidate_slot
}

resource "aws_ssm_parameter" "retired_slot" {
  count = var.retired_slot == null ? 0 : 1

  name  = "${local.parameter_root}/control/retired-slot"
  type  = "String"
  value = var.retired_slot
}

resource "aws_ssm_parameter" "retire_after" {
  count = var.retire_after == null ? 0 : 1

  name  = "${local.parameter_root}/control/retire-after"
  type  = "String"
  value = var.retire_after
}

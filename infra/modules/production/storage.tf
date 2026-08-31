resource "aws_s3_bucket" "operations" {
  bucket = local.operations_bucket

  lifecycle {
    prevent_destroy = true
  }

  tags = local.common_tags
}

resource "aws_s3_bucket_public_access_block" "operations" {
  bucket                  = aws_s3_bucket.operations.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "operations" {
  bucket = aws_s3_bucket.operations.id
  rule { object_ownership = "BucketOwnerEnforced" }
}

resource "aws_s3_bucket_versioning" "operations" {
  bucket = aws_s3_bucket.operations.id
  versioning_configuration { status = "Enabled" }
}

#trivy:ignore:AVD-AWS-0132 AWS-managed SSE is deliberate: this private operations bucket does not require a customer-managed KMS key.
resource "aws_s3_bucket_server_side_encryption_configuration" "operations" {
  bucket = aws_s3_bucket.operations.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "operations" {
  bucket = aws_s3_bucket.operations.id

  rule {
    id     = "weekly-backups-84-days"
    status = "Enabled"
    filter { prefix = "backups/weekly/" }
    expiration { days = 84 }
    noncurrent_version_expiration { noncurrent_days = 84 }
    abort_incomplete_multipart_upload { days_after_initiation = 7 }
  }

  rule {
    id     = "predeploy-backups-30-days"
    status = "Enabled"
    filter { prefix = "backups/predeploy/" }
    expiration { days = 30 }
    noncurrent_version_expiration { noncurrent_days = 30 }
    abort_incomplete_multipart_upload { days_after_initiation = 7 }
  }

  rule {
    id     = "release-bundles"
    status = "Enabled"
    filter { prefix = "releases/" }
    noncurrent_version_expiration { noncurrent_days = 90 }
    abort_incomplete_multipart_upload { days_after_initiation = 7 }
  }

  rule {
    id     = "restore-drill-markers"
    status = "Enabled"
    filter { prefix = "backups/restore-drills/" }
    expiration { days = 400 }
    noncurrent_version_expiration { noncurrent_days = 90 }
    abort_incomplete_multipart_upload { days_after_initiation = 7 }
  }
}

resource "aws_s3_bucket_policy" "operations" {
  bucket = aws_s3_bucket.operations.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [aws_s3_bucket.operations.arn, "${aws_s3_bucket.operations.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
}

# Atomic, non-secret deployment state intentionally drifts after deploys.
resource "aws_ssm_parameter" "deployment_state" {
  name = "${local.parameter_root}/deployment/state"
  type = "String"
  value = jsonencode({
    version         = "0.0.0"
    image_digest    = ""
    bundle_key      = ""
    bundle_checksum = ""
  })
  lifecycle { ignore_changes = [value] }
}

# Bootstrap credentials are operator-provided runtime secrets. Terraform uses
# write-only fields so neither value is retained in plan files or state.
resource "aws_ssm_parameter" "bootstrap_jwt" {
  name             = local.secret_parameter_names.bootstrap_jwt
  type             = "SecureString"
  tier             = "Standard"
  overwrite        = true
  value_wo         = var.bootstrap_jwt
  value_wo_version = var.bootstrap_token_generation

  depends_on = [aws_iam_role_policy.github_apply]

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_ssm_parameter" "bootstrap_refresh_token" {
  name             = local.secret_parameter_names.bootstrap_refresh_token
  type             = "SecureString"
  tier             = "Standard"
  overwrite        = true
  value_wo         = var.bootstrap_refresh_token
  value_wo_version = var.bootstrap_token_generation

  depends_on = [aws_iam_role_policy.github_apply]

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_ssm_parameter" "operations_bucket" {
  name  = "${local.parameter_root}/config/operations-bucket"
  type  = "String"
  value = aws_s3_bucket.operations.id
}

# The endpoint is non-secret runtime configuration. Terraform creates a
# deterministic path but ignores the operator-entered endpoint afterward.
resource "aws_ssm_parameter" "supabase_url" {
  name  = "${local.parameter_root}/config/supabase-url"
  type  = "String"
  value = "https://configuration-required.invalid"

  lifecycle { ignore_changes = [value] }
}

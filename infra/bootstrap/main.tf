locals {
  tags = {
    Project     = "battlevive-bot"
    Environment = "production"
    ManagedBy   = "terraform-bootstrap"
  }
}

resource "random_id" "state_suffix" {
  byte_length = 4
}

resource "aws_s3_bucket" "state" {
  bucket = "battlevive-bot-tfstate-${var.account_id}-${random_id.state_suffix.hex}"

  lifecycle {
    prevent_destroy = true
  }

  tags = local.tags
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket = aws_s3_bucket.state.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id

  versioning_configuration {
    status = "Enabled"
  }
}

#trivy:ignore:AVD-AWS-0132 AWS-managed SSE is deliberate: the state bucket is private, versioned, and does not justify a customer-managed key.
resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_policy" "state" {
  bucket = aws_s3_bucket.state.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource = [
          aws_s3_bucket.state.arn,
          "${aws_s3_bucket.state.arn}/*",
        ]
        Condition = {
          Bool = { "aws:SecureTransport" = "false" }
        }
      },
    ]
  })
}

# Blue and green retain independent state so a retired slot can be destroyed
# without affecting the active slot or its replacement's state history.
resource "aws_s3_bucket" "slot_state" {
  for_each = toset(["blue", "green"])

  bucket = "battlevive-bot-tfstate-${each.key}-${var.account_id}-${random_id.state_suffix.hex}"

  lifecycle {
    prevent_destroy = true
  }

  tags = merge(local.tags, { Slot = each.key })
}

resource "aws_s3_bucket_public_access_block" "slot_state" {
  for_each = aws_s3_bucket.slot_state

  bucket                  = each.value.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "slot_state" {
  for_each = aws_s3_bucket.slot_state

  bucket = each.value.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_versioning" "slot_state" {
  for_each = aws_s3_bucket.slot_state

  bucket = each.value.id

  versioning_configuration {
    status = "Enabled"
  }
}

#trivy:ignore:AVD-AWS-0132 AWS-managed SSE is deliberate: slot state is private, versioned, and does not justify a customer-managed key.
resource "aws_s3_bucket_server_side_encryption_configuration" "slot_state" {
  for_each = aws_s3_bucket.slot_state

  bucket = each.value.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_policy" "slot_state" {
  for_each = aws_s3_bucket.slot_state

  bucket = each.value.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource = [
          each.value.arn,
          "${each.value.arn}/*",
        ]
        Condition = {
          Bool = { "aws:SecureTransport" = "false" }
        }
      },
    ]
  })
}

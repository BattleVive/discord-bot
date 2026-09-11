output "state_bucket" {
  description = "Pass this value to production init as backend-config bucket."
  value       = aws_s3_bucket.state.id
}

output "backend_keys" {
  value = {
    control = "battlevive-bot/control.tfstate"
    blue    = "battlevive-bot/blue.tfstate"
    green   = "battlevive-bot/green.tfstate"
  }
}

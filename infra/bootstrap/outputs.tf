output "state_bucket" {
  description = "Pass this value to production init as backend-config bucket."
  value       = aws_s3_bucket.state.id
}

output "backend_key" {
  value = "battlevive-bot/production.tfstate"
}

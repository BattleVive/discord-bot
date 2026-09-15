output "state_bucket" {
  description = "Control-plane state bucket for the production root."
  value       = aws_s3_bucket.state.id
}

output "slot_state_buckets" {
  description = "Persistent state buckets for independently managed blue and green slots."
  value       = { for slot, bucket in aws_s3_bucket.slot_state : slot => bucket.id }
}

output "backend_keys" {
  value = {
    control = "battlevive-bot/control.tfstate"
    blue    = "battlevive-bot/blue.tfstate"
    green   = "battlevive-bot/green.tfstate"
  }
}

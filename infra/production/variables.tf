variable "aws_region" { type = string }
variable "operations_bucket_suffix" { type = string }
variable "active_slot" {
  type = string
  validation {
    condition     = contains(["blue", "green"], var.active_slot)
    error_message = "active_slot must be blue or green."
  }
}
variable "release_candidate_slot" {
  type = string
  validation {
    condition     = contains(["blue", "green"], var.release_candidate_slot)
    error_message = "release_candidate_slot must be blue or green."
  }
}

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
variable "retired_slot" {
  type      = string
  default   = null
  nullable  = true
  sensitive = false

  validation {
    condition     = var.retired_slot == null || contains(["blue", "green"], var.retired_slot)
    error_message = "retired_slot must be blue, green, or null."
  }
}
variable "retire_after" {
  type      = string
  default   = null
  nullable  = true
  sensitive = false

  validation {
    condition     = var.retire_after == null || can(formatdate("YYYY-MM-DD'T'hh:mm:ssZ", var.retire_after))
    error_message = "retire_after must be an RFC3339 UTC timestamp or null."
  }
}

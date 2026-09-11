locals {
  name           = "${var.name_prefix}-${var.slot}"
  parameter_root = "${var.parameter_root}/${var.slot}"
  tags           = { Project = var.project, Slot = var.slot, ManagedBy = "terraform" }
}

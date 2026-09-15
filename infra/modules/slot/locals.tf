locals {
  name           = "${var.name_prefix}-${var.slot}"
  parameter_root = "${var.parameter_root}/${var.slot}"
  network_cidr   = { blue = "10.80.0.0/16", green = "10.81.0.0/16" }[var.slot]
  tags           = { Project = var.project, Slot = var.slot, ManagedBy = "terraform" }
}

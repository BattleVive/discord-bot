data "aws_availability_zones" "available" { state = "available" }
data "aws_ssm_parameter" "al2023_arm64" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
}
data "aws_caller_identity" "current" {}

resource "aws_vpc" "slot" {
  cidr_block           = var.network_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true
  tags                 = merge(local.tags, { Name = "${local.name}-vpc" })
}

resource "aws_internet_gateway" "slot" {
  vpc_id = aws_vpc.slot.id
  tags   = merge(local.tags, { Name = "${local.name}-igw" })
}

resource "aws_route_table" "host" {
  vpc_id = aws_vpc.slot.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.slot.id
  }
  tags = merge(local.tags, { Name = "${local.name}-host" })
}

resource "aws_subnet" "slot" {
  count                   = 2
  vpc_id                  = aws_vpc.slot.id
  cidr_block              = cidrsubnet(var.network_cidr, 8, count.index)
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true
  tags                    = merge(local.tags, { Name = "${local.name}-${count.index}" })
}

resource "aws_route_table_association" "host" {
  count          = length(aws_subnet.slot)
  subnet_id      = aws_subnet.slot[count.index].id
  route_table_id = aws_route_table.host.id
}

resource "aws_security_group" "host" {
  name   = "${local.name}-host"
  vpc_id = aws_vpc.slot.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "postgres" {
  name   = "${local.name}-postgres"
  vpc_id = aws_vpc.slot.id

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.host.id]
  }
}
resource "aws_db_subnet_group" "postgres" {
  name       = "${local.name}-postgres"
  subnet_ids = aws_subnet.slot[*].id
}
resource "aws_db_instance" "postgres" {
  identifier                  = "${local.name}-postgres"
  engine                      = "postgres"
  instance_class              = "db.t4g.micro"
  allocated_storage           = 20
  storage_type                = "gp3"
  storage_encrypted           = false
  db_subnet_group_name        = aws_db_subnet_group.postgres.name
  vpc_security_group_ids      = [aws_security_group.postgres.id]
  publicly_accessible         = false
  multi_az                    = false
  skip_final_snapshot         = true
  snapshot_identifier         = var.rds_snapshot_identifier
  username                    = var.rds_snapshot_identifier == null ? "battlevive" : null
  manage_master_user_password = true
}
resource "aws_instance" "host" {
  ami                         = data.aws_ssm_parameter.al2023_arm64.value
  availability_zone           = aws_subnet.slot[0].availability_zone
  instance_type               = "t4g.micro"
  iam_instance_profile        = aws_iam_instance_profile.host.name
  subnet_id                   = aws_subnet.slot[0].id
  vpc_security_group_ids      = [aws_security_group.host.id]
  user_data                   = file("${path.module}/bootstrap-runtime.sh")
  user_data_replace_on_change = true
  root_block_device {
    encrypted   = false
    volume_type = "gp3"
    volume_size = 16
  }
  tags = merge(local.tags, { SSMTarget = local.name })
}

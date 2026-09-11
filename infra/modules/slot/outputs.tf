output "instance_id" { value = aws_instance.host.id }
output "rds_identifier" { value = aws_db_instance.postgres.identifier }

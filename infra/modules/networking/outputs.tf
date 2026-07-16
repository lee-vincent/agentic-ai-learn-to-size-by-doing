output "vpc_id" {
  value = aws_vpc.this.id
}

output "subnet_id" {
  value = aws_subnet.main.id
}

output "availability_zone" {
  value = local.gpu_az
}

output "security_group_id" {
  value = aws_security_group.instance.id
}

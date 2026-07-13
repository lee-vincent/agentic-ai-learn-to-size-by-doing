output "vpc_id" {
  value = aws_vpc.this.id
}

output "subnet_id" {
  value = aws_subnet.cluster.id
}

output "availability_zone" {
  value = local.gpu_az
}

output "cluster_security_group_id" {
  value = aws_security_group.cluster.id
}

output "fsx_security_group_id" {
  value = aws_security_group.fsx.id
}

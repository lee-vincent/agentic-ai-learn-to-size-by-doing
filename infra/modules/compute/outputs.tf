output "instance_ids" {
  value = aws_instance.gpu[*].id
}

output "private_ips" {
  value = aws_instance.gpu[*].private_ip
}

output "public_ips" {
  value = aws_eip.gpu[*].public_ip
}

output "placement_group_name" {
  value = aws_placement_group.gpu_cluster.name
}

output "vcpus_per_node" {
  value = data.aws_ec2_instance_type.gpu.default_vcpus
}

locals {
  # `gpus` is a set (no stable index), so convert to a list before indexing.
  # Every current GPU instance type has exactly one homogeneous GPU entry
  # (one manufacturer/model per instance type), so gpus[0] is the whole
  # story here, not an arbitrary pick among heterogeneous GPUs.
  gpu_info = tolist(data.aws_ec2_instance_type.gpu.gpus)
}

output "gpu_count_per_node" {
  value = length(local.gpu_info) > 0 ? local.gpu_info[0].count : 0
}

output "gpu_memory_mib_each" {
  value = length(local.gpu_info) > 0 ? local.gpu_info[0].memory_size : 0
}

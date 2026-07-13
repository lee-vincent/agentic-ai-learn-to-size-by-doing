# Facts about the chosen instance type, fetched live rather than
# hardcoded, so vCPU-quota math and VRAM totals in the root outputs always
# reflect whatever gpu_instance_type is actually configured.
data "aws_ec2_instance_type" "gpu" {
  instance_type = var.gpu_instance_type
}

# AWS Deep Learning Base OSS Nvidia Driver GPU AMI (Ubuntu 22.04). Its
# description (verified 2026-07-13) explicitly lists support for
# G4dn/G5/G6/G6e/G7/G7e/P4d/P4de/P5/P5e/P5en/P6-B200/P6-B300 and ships with
# the NVIDIA driver, Docker, and NVIDIA Container Toolkit preinstalled.
# Fetched via the "latest" SSM path so `terraform plan` always evaluates
# against whatever AWS currently publishes, instead of a pinned AMI ID that
# silently goes stale.
data "aws_ssm_parameter" "dlami" {
  name = "/aws/service/deeplearning/ami/x86_64/base-oss-nvidia-driver-gpu-ubuntu-22.04/latest/ami-id"
}

# Cluster placement group: packs all GPU nodes onto the same low-latency
# network spine, which is what actually makes EFA/NCCL cross-node collective
# ops fast. Required for meaningful multi-node tensor/pipeline benchmarking.
resource "aws_placement_group" "gpu_cluster" {
  name     = "${var.project}-gpu-cluster"
  strategy = "cluster"

  tags = merge(var.tags, { Name = "${var.project}-gpu-cluster-pg" })
}

# One EFA-enabled ENI per node. aws_instance's inline network_interface
# block only supports interface_type = "secondary", so the EFA interface
# has to be created as its own aws_network_interface resource (interface_
# type = "efa") and then attached as the instance's primary interface here.
resource "aws_network_interface" "gpu" {
  count = var.gpu_node_count

  subnet_id       = var.subnet_id
  security_groups = [var.cluster_security_group_id]
  interface_type  = "efa"

  tags = merge(var.tags, { Name = "${var.project}-gpu-${count.index}-efa0" })
}

# Direct public IP per node (see modules/networking for the NAT-vs-EIP cost
# rationale). Needed for HF Hub downloads, container pulls, and the SSM
# agent's outbound connection, since there is no NAT Gateway in this VPC.
resource "aws_eip" "gpu" {
  count = var.gpu_node_count

  domain            = "vpc"
  network_interface = aws_network_interface.gpu[count.index].id

  tags = merge(var.tags, { Name = "${var.project}-gpu-${count.index}-eip" })
}

resource "aws_instance" "gpu" {
  count = var.gpu_node_count

  ami                  = data.aws_ssm_parameter.dlami.value
  instance_type        = var.gpu_instance_type
  placement_group      = aws_placement_group.gpu_cluster.name
  iam_instance_profile = var.instance_profile_name
  key_name             = var.ssh_key_name

  network_interface {
    network_interface_id = aws_network_interface.gpu[count.index].id
    device_index         = 0
  }

  root_block_device {
    volume_size           = var.root_volume_size_gb
    volume_type           = "gp3"
    delete_on_termination = true
    encrypted             = true
  }

  metadata_options {
    http_tokens   = "required" # IMDSv2 only
    http_endpoint = "enabled"
  }

  user_data = templatefile("${path.module}/templates/user_data.sh.tpl", {
    node_index        = count.index
    gpu_instance_type = var.gpu_instance_type
    fsx_dns_name      = var.fsx_dns_name
    fsx_mount_name    = var.fsx_mount_name
  })

  tags = merge(var.tags, {
    Name = "${var.project}-gpu-${count.index}"
    Role = count.index == 0 ? "ray-head" : "ray-worker"
  })

  # EFA network interfaces need every node in the placement group present
  # before capacity is committed; if capacity for this instance type/AZ is
  # constrained, AWS will reject the launch rather than Terraform silently
  # doing something wrong -- surfaced clearly at apply time.
  lifecycle {
    create_before_destroy = false
  }
}

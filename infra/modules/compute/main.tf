# Facts about the chosen instance type, fetched live rather than
# hardcoded, so vCPU-quota math and VRAM totals in the root outputs always
# reflect whatever gpu_instance_type is actually configured.
data "aws_ec2_instance_type" "gpu" {
  instance_type = var.gpu_instance_type
}

locals {
  # Detect EFA support from the API rather than a hardcoded per-type list --
  # e.g. p5.48xlarge is EFA-capable (EfaSupported=true, verified 2026-07-15
  # via `aws ec2 describe-instance-types`), g6e.4xlarge is not
  # (EfaSupported=false, same verification). Driving this off the data
  # source means switching gpu_instance_type to any other family (G, P, or
  # future types) never silently tries to attach an EFA ENI to hardware that
  # doesn't support it.
  efa_supported = data.aws_ec2_instance_type.gpu.efa_supported
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

# One ENI per node, attached as the instance's primary interface.
# aws_instance's inline network_interface block only supports
# interface_type = "secondary", so this has to be created as its own
# aws_network_interface resource and then attached below.
#
# interface_type is only set to "efa" when the selected gpu_instance_type
# actually supports it (local.efa_supported, above); otherwise it's left
# null so the provider defaults to a standard ENA interface ("interface").
# This lets gpu_instance_type be switched to a non-EFA type (e.g.
# g6e.4xlarge, used for quota-constrained bring-up per
# infra/examples/g6e-multinode.tfvars) without the apply failing -- EFA
# interfaces can only be attached to instance types that support EFA.
#
# The cluster security group's self-referencing all-traffic rule
# (modules/networking, aws_vpc_security_group_ingress_rule.cluster_self_all,
# ip_protocol = "-1") is not EFA-specific -- it works identically for
# standard ENA cross-node traffic, so Ray/NCCL-over-TCP still functions on
# the non-EFA path (at ENA throughput/latency, not EFA's).
resource "aws_network_interface" "gpu" {
  count = var.gpu_node_count

  subnet_id       = var.subnet_id
  security_groups = [var.cluster_security_group_id]
  interface_type  = local.efa_supported ? "efa" : null

  tags = merge(var.tags, {
    Name = "${var.project}-gpu-${count.index}-${local.efa_supported ? "efa0" : "eni0"}"
  })
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
    efa_expected      = local.efa_supported
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

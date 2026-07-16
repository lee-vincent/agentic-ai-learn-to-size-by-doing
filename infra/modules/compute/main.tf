# Facts about the chosen instance type, fetched live rather than hardcoded,
# so vCPU-quota math and VRAM totals in the root outputs always reflect
# whatever gpu_instance_type is actually configured.
data "aws_ec2_instance_type" "gpu" {
  instance_type = var.gpu_instance_type
}

# AWS Deep Learning Base OSS Nvidia Driver GPU AMI (Ubuntu 22.04). Its
# description (verified live) explicitly lists G6e support and ships with
# the NVIDIA driver, Docker, and NVIDIA Container Toolkit preinstalled.
# Fetched via the "latest" SSM path so `terraform plan` always evaluates
# against whatever AWS currently publishes, instead of a pinned AMI ID that
# silently goes stale.
data "aws_ssm_parameter" "dlami" {
  name = "/aws/service/deeplearning/ami/x86_64/base-oss-nvidia-driver-gpu-ubuntu-22.04/latest/ami-id"
}

resource "aws_instance" "gpu" {
  ami                    = data.aws_ssm_parameter.dlami.value
  instance_type          = var.gpu_instance_type
  subnet_id              = var.subnet_id
  vpc_security_group_ids = [var.security_group_id]
  iam_instance_profile   = var.instance_profile_name
  key_name               = var.ssh_key_name

  # No `associate_public_ip_address` here -- the Elastic IP below (attached
  # directly to this instance, not to a separate network interface resource)
  # gives the instance a stable public IP without needing a hand-managed ENI.
  # There's exactly one instance, so a single aws_instance resource (no
  # `count`) is the simplest correct shape -- no placement group, no
  # per-node ENI/EFA branching, no multi-node anything.

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
    gpu_instance_type = var.gpu_instance_type
  })

  tags = merge(var.tags, { Name = "${var.project}-gpu" })
}

# Direct public IP for the instance (see modules/networking for the
# NAT-vs-EIP cost rationale). Needed for HF Hub downloads, container pulls,
# and the SSM agent's outbound connection, since there is no NAT Gateway in
# this VPC. Attached straight to the instance -- no separate
# aws_network_interface resource needed for a single, non-EFA instance.
resource "aws_eip" "gpu" {
  domain   = "vpc"
  instance = aws_instance.gpu.id

  tags = merge(var.tags, { Name = "${var.project}-gpu-eip" })
}

# Discover which AZ in this region actually offers the chosen GPU instance
# type. Not every AZ in every region carries every instance family, so we
# pick the first AZ that offers it rather than hardcoding one.
data "aws_ec2_instance_type_offerings" "gpu_az" {
  filter {
    name   = "instance-type"
    values = [var.gpu_instance_type]
  }

  location_type = "availability-zone"
}

locals {
  # locations[0] is deterministic for a given account/region/instance-type
  # (the API returns AZs in a stable order), so this is safe to use directly
  # in the subnet's availability_zone argument.
  gpu_az = data.aws_ec2_instance_type_offerings.gpu_az.locations[0]
}

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = merge(var.tags, { Name = "${var.project}-vpc" })
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id

  tags = merge(var.tags, { Name = "${var.project}-igw" })
}

# Single subnet for the single GPU instance -- no cluster placement group, no
# EFA, no multi-node, so there's no reason for more than one subnet/AZ here.
#
# The instance gets a direct public IP (via the Elastic IP in the compute
# module) instead of a NAT Gateway. That's a deliberate cost/simplicity
# trade for a lab that needs to pull a multi-GB model checkpoint and
# container image: NAT Gateway is $0.045/hr plus $0.045/GB *processed* on top
# of standard data transfer, whereas a public IP is a flat $0.005/hr
# (verified via the AWS Price List API, us-east-1, 2026-07-16) with no
# per-GB processing fee. Inbound exposure is controlled entirely by the
# security group below (nothing open to 0.0.0.0/0 by default) plus IMDSv2 +
# SSM Session Manager instead of open SSH. See infra/README.md for the full
# tradeoff writeup.
resource "aws_subnet" "main" {
  vpc_id            = aws_vpc.this.id
  cidr_block        = var.subnet_cidr
  availability_zone = local.gpu_az

  tags = merge(var.tags, { Name = "${var.project}-subnet" })
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }

  tags = merge(var.tags, { Name = "${var.project}-public-rt" })
}

resource "aws_route_table_association" "main" {
  subnet_id      = aws_subnet.main.id
  route_table_id = aws_route_table.public.id
}

# Free S3 gateway endpoint -- ECR image layers are stored in S3, and any
# S3-hosted apt/pip mirrors benefit too. Traffic to S3 doesn't need to leave
# the AWS network or count against internet egress, regardless of the
# no-NAT decision above. Zero cost, so kept even though the model weights
# themselves come straight from the HuggingFace Hub rather than a staging
# bucket (see infra/README.md "Storage choice" for that decision).
resource "aws_vpc_endpoint" "s3" {
  vpc_id          = aws_vpc.this.id
  service_name    = "com.amazonaws.${data.aws_region.current.region}.s3"
  route_table_ids = [aws_route_table.public.id]

  tags = merge(var.tags, { Name = "${var.project}-s3-endpoint" })
}

data "aws_region" "current" {}

# Single security group for the instance. Nothing open to 0.0.0.0/0 by
# default -- SSH is opt-in via ssh_ingress_cidrs (empty by default, use SSM
# Session Manager instead), and the vLLM/monitoring service ports are scoped
# to internal_ingress_cidrs (defaults to the VPC CIDR only). There is no
# self-referencing all-traffic rule here (unlike the old multi-node design's
# EFA/NCCL/Ray requirement) -- a single instance has no cluster peers to talk
# to over the network.
resource "aws_security_group" "instance" {
  name        = "${var.project}-instance"
  description = "Single GPU instance: scoped service ports, opt-in SSH, unrestricted egress"
  vpc_id      = aws_vpc.this.id

  tags = merge(var.tags, { Name = "${var.project}-instance-sg" })
}

resource "aws_vpc_security_group_egress_rule" "egress_all" {
  security_group_id = aws_security_group.instance.id
  description       = "Unrestricted egress (HF Hub downloads, container pulls, package installs)"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

resource "aws_vpc_security_group_ingress_rule" "ssh" {
  for_each = toset(var.ssh_ingress_cidrs)

  security_group_id = aws_security_group.instance.id
  description       = "SSH (opt-in via ssh_ingress_cidrs; empty by default -- use SSM Session Manager)"
  cidr_ipv4         = each.value
  from_port         = 22
  to_port           = 22
  ip_protocol       = "tcp"
}

# Ports later phases (serving/loadgen/monitoring) will use, scoped to
# internal_ingress_cidrs (defaults to the VPC CIDR only, never the public
# internet): vLLM OpenAI-compatible API (8000), Prometheus (9090), Grafana
# (3000), Node Exporter (9100), DCGM Exporter (9400).
locals {
  internal_tcp_ports = {
    vllm_api      = 8000
    prometheus    = 9090
    grafana       = 3000
    node_exporter = 9100
    dcgm_exporter = 9400
  }
}

resource "aws_vpc_security_group_ingress_rule" "internal_ports" {
  for_each = { for pair in setproduct(keys(local.internal_tcp_ports), var.internal_ingress_cidrs) : "${pair[0]}-${pair[1]}" => {
    port = local.internal_tcp_ports[pair[0]]
    cidr = pair[1]
    name = pair[0]
  } }

  security_group_id = aws_security_group.instance.id
  description       = "${each.value.name} (internal only)"
  cidr_ipv4         = each.value.cidr
  from_port         = each.value.port
  to_port           = each.value.port
  ip_protocol       = "tcp"
}

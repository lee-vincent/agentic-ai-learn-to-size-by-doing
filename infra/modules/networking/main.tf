# Discover which AZ in this region actually offers the chosen GPU instance
# type. Not every AZ in every region carries every instance family, and a
# cluster placement group + EFA setup wants every node in one AZ/subnet
# anyway, so we pick the first AZ that offers it rather than hardcoding one.
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

# Single subnet for the whole GPU cluster: cluster placement groups and EFA
# both want same-AZ, and per SPEC.md the smaller two lineup models run on a
# subset of these same nodes, so there's no separate subnet for them.
#
# Nodes get direct public IPs (via per-node Elastic IPs in the compute
# module) instead of a NAT Gateway. That's a deliberate cost/simplicity
# trade for a lab that needs to pull multi-hundred-GB model checkpoints:
# NAT Gateway is $0.045/hr plus $0.045/GB *processed* on top of standard
# data transfer, which adds up fast at this checkpoint size, whereas a
# public IP is a flat $0.005/hr (verified via the AWS Price List API,
# us-east-1, 2026-07-13) with no per-GB processing fee. Inbound exposure is
# controlled entirely by the security group below (nothing open to
# 0.0.0.0/0 by default) plus IMDSv2 + SSM Session Manager instead of open
# SSH. See infra/README.md for the full tradeoff writeup.
resource "aws_subnet" "cluster" {
  vpc_id            = aws_vpc.this.id
  cidr_block        = var.cluster_subnet_cidr
  availability_zone = local.gpu_az

  tags = merge(var.tags, { Name = "${var.project}-cluster-subnet" })
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }

  tags = merge(var.tags, { Name = "${var.project}-public-rt" })
}

resource "aws_route_table_association" "cluster" {
  subnet_id      = aws_subnet.cluster.id
  route_table_id = aws_route_table.public.id
}

# Free S3 gateway endpoint -- traffic to the model-weights bucket (and any
# S3-hosted apt/pip mirrors) doesn't need to leave the AWS network or count
# against anything, regardless of the no-NAT decision above.
resource "aws_vpc_endpoint" "s3" {
  vpc_id          = aws_vpc.this.id
  service_name    = "com.amazonaws.${data.aws_region.current.region}.s3"
  route_table_ids = [aws_route_table.public.id]

  tags = merge(var.tags, { Name = "${var.project}-s3-endpoint" })
}

data "aws_region" "current" {}

# Cluster security group: EFA/NCCL/Ray/gloo all need unrestricted traffic
# between cluster members (AWS's own EFA setup guides call for an
# all-traffic self-referencing rule -- trying to enumerate individual ports
# for NCCL's ephemeral rendezvous ports is a known footgun). Nothing else is
# open by default.
resource "aws_security_group" "cluster" {
  name        = "${var.project}-cluster"
  description = "GPU cluster nodes: EFA/NCCL/Ray inter-node traffic + scoped service ports"
  vpc_id      = aws_vpc.this.id

  tags = merge(var.tags, { Name = "${var.project}-cluster-sg" })
}

resource "aws_vpc_security_group_ingress_rule" "cluster_self_all" {
  security_group_id            = aws_security_group.cluster.id
  description                  = "All traffic between cluster nodes (EFA/NCCL/Ray/gloo rendezvous)"
  referenced_security_group_id = aws_security_group.cluster.id
  ip_protocol                  = "-1"
}

resource "aws_vpc_security_group_egress_rule" "cluster_egress_all" {
  security_group_id = aws_security_group.cluster.id
  description       = "Unrestricted egress (HF Hub downloads, container pulls, package installs)"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

resource "aws_vpc_security_group_ingress_rule" "cluster_ssh" {
  for_each = toset(var.ssh_ingress_cidrs)

  security_group_id = aws_security_group.cluster.id
  description       = "SSH (opt-in via ssh_ingress_cidrs; empty by default -- use SSM Session Manager)"
  cidr_ipv4         = each.value
  from_port         = 22
  to_port           = 22
  ip_protocol       = "tcp"
}

# Ports future phases (serving/loadgen/monitoring) will use, scoped to
# internal_ingress_cidrs (defaults to the VPC CIDR only, never the public
# internet): vLLM OpenAI-compatible API (8000), Ray GCS/dashboard
# (6379/8265), Prometheus (9090), Grafana (3000), Node Exporter (9100),
# DCGM Exporter (9400).
locals {
  internal_tcp_ports = {
    vllm_api      = 8000
    ray_gcs       = 6379
    ray_dashboard = 8265
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

  security_group_id = aws_security_group.cluster.id
  description       = "${each.value.name} (internal only)"
  cidr_ipv4         = each.value.cidr
  from_port         = each.value.port
  to_port           = each.value.port
  ip_protocol       = "tcp"
}

# FSx for Lustre needs 988/tcp (Lustre protocol) and the 1021-1023/tcp
# ephemeral range from clients, per AWS's documented FSx Lustre SG
# requirements.
resource "aws_security_group" "fsx" {
  name        = "${var.project}-fsx"
  description = "FSx for Lustre client access from the GPU cluster"
  vpc_id      = aws_vpc.this.id

  tags = merge(var.tags, { Name = "${var.project}-fsx-sg" })
}

resource "aws_vpc_security_group_ingress_rule" "fsx_lustre" {
  security_group_id            = aws_security_group.fsx.id
  description                  = "Lustre protocol from cluster nodes"
  referenced_security_group_id = aws_security_group.cluster.id
  from_port                    = 988
  to_port                      = 988
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "fsx_lustre_ephemeral" {
  security_group_id            = aws_security_group.fsx.id
  description                  = "Lustre ephemeral range from cluster nodes"
  referenced_security_group_id = aws_security_group.cluster.id
  from_port                    = 1021
  to_port                      = 1023
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "fsx_egress_all" {
  security_group_id = aws_security_group.fsx.id
  description       = "Unrestricted egress"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

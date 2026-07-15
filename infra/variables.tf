variable "aws_region" {
  description = "AWS region for the cluster. us-east-1 has the deepest P5/P4d capacity pools and is where pricing/quota below were verified."
  type        = string
  default     = "us-east-1"
}

variable "project" {
  description = "Short name used to prefix/tag every resource in this stack."
  type        = string
  default     = "gpu-sizing-lab"
}

variable "environment" {
  description = "Free-form environment tag (e.g. lab, dev)."
  type        = string
  default     = "lab"
}

variable "vpc_cidr" {
  description = "CIDR block for the lab VPC."
  type        = string
  default     = "10.42.0.0/16"
}

variable "cluster_subnet_cidr" {
  description = "CIDR for the single subnet the GPU cluster placement group lives in. Cluster placement groups and EFA both want everything in one AZ/subnet."
  type        = string
  default     = "10.42.1.0/24"
}

# --- GPU compute sizing ---------------------------------------------------
# See infra/README.md "Instance choice" for the full derivation. Short
# version: Qwen3.5-397B-A17B's real checkpoint sizes (verified against the
# HF repos, not a blog estimate) are ~751 GiB BF16 / ~378 GiB FP8 / ~220 GiB
# INT4. An 8x80GiB H100 node (p5.48xlarge, 640 GiB VRAM) fits FP8 and INT4
# on a single node but not BF16 -- which is exactly the SPEC.md back-of-
# envelope this lab is built around. Multi-node is provisioned regardless,
# per SPEC.md/CLAUDE.md, to exercise cross-node pipeline/data parallel.
variable "gpu_instance_type" {
  description = "EC2 instance type for GPU cluster nodes. Default is the P-family p5.48xlarge (see infra/README.md for the current EFA + pricing verification). A G-family alternative (g6e.4xlarge) is also supported for quota-constrained bring-up while a P-family vCPU quota increase is pending -- see infra/examples/g6e-multinode.tfvars and the README's 'Alternate G-family profile' section. EFA attachment (modules/compute) and the vCPU quota check (main.tf) both key off this value automatically via the aws_ec2_instance_type data source, so other instance types are mechanically supported too, but only p5.48xlarge and g6e.4xlarge are verified/documented here."
  type        = string
  default     = "p5.48xlarge"
}

variable "gpu_node_count" {
  description = "Number of homogeneous GPU nodes in the cluster placement group. Must be >= 2 -- multi-node is a deliberate requirement (see CLAUDE.md/SPEC.md), not something to collapse to 1 even though FP8/INT4 weights alone would fit on a single node."
  type        = number
  default     = 2

  validation {
    condition     = var.gpu_node_count >= 2
    error_message = "gpu_node_count must be >= 2: multi-node is a deliberate requirement for this lab, to exercise cross-node pipeline/data parallel (see SPEC.md and CLAUDE.md)."
  }
}

variable "root_volume_size_gb" {
  description = "Root EBS volume size (GiB) per GPU node. Model weights live on shared FSx, not the root volume, so this only needs to hold the OS, container images, and vLLM/Ray installs."
  type        = number
  default     = 300
}

variable "ssh_ingress_cidrs" {
  description = "CIDR blocks allowed to SSH (22/tcp) into cluster nodes. Left empty by default -- use AWS Systems Manager Session Manager instead (the instance role already has AmazonSSMManagedInstanceCore). Only set this if you specifically need direct SSH."
  type        = list(string)
  default     = []
}

variable "internal_ingress_cidrs" {
  description = "CIDR blocks allowed to reach vLLM/Ray/monitoring ports (see modules/networking for the port list). Defaults to the VPC CIDR only -- nothing is exposed to the public internet by default."
  type        = list(string)
  default     = null # resolved to [var.vpc_cidr] in main.tf when left null
}

variable "ssh_key_name" {
  description = "Optional existing EC2 key pair name to attach to GPU nodes for SSH fallback. Leave null to rely solely on SSM Session Manager."
  type        = string
  default     = null
}

# --- Shared storage --------------------------------------------------------
variable "fsx_storage_capacity_gib" {
  description = "FSx for Lustre capacity in GiB. Must comfortably hold whichever precision variants of the model lineup you keep cached concurrently -- see infra/README.md for the sizing math (BF16+FP8+INT4 of the 397B-A17B model alone is ~1.35 TiB)."
  type        = number
  default     = 2400
}

variable "fsx_per_unit_storage_throughput" {
  description = "FSx for Lustre PERSISTENT_2 SSD throughput tier (MB/s per TiB). Valid values: 125, 250, 500, 1000."
  type        = number
  default     = 125
}

# --- HF_TOKEN secret slot ---------------------------------------------------
variable "hf_token_parameter_name" {
  description = "SSM Parameter Store path for the (empty at plan/apply time) HuggingFace token slot. The value is injected out-of-band by a human -- see infra/README.md."
  type        = string
  default     = "/gpu-sizing-lab/hf-token"
}

variable "aws_region" {
  description = "AWS region for the lab. us-east-1 is where pricing/quota/availability below were verified live (2026-07-16)."
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

variable "subnet_cidr" {
  description = "CIDR for the single public subnet the GPU instance lives in."
  type        = string
  default     = "10.42.1.0/24"
}

# --- GPU compute sizing ---------------------------------------------------
# See infra/README.md "Instance choice" for the full derivation. Short
# version: this is a single-node, single-GPU lab on purpose (see SPEC.md's
# "Scope history") -- one instance, no cluster placement group, no EFA, no
# multi-node anything. g6e.2xlarge (1x L40S, 44.7 GiB VRAM, 8 vCPU, 64 GiB
# RAM) is the default: verified live (2026-07-16, us-east-1) at $2.24208/hr
# on-demand, and Qwen3.6-27B fits its VRAM at FP8 (~29 GiB weights, ~15 GiB
# left for KV cache) -- see infra/README.md for the checkpoint-size
# verification.
variable "gpu_instance_type" {
  description = "EC2 instance type for the single GPU instance. Default g6e.2xlarge (1x L40S, 8 vCPU/64 GiB RAM) -- see infra/README.md for verified pricing/availability. g6e.xlarge (4 vCPU/32 GiB) is cheaper but risky for loading a 27B checkpoint plus request/vision preprocessing headroom; g6e.4xlarge (16 vCPU/128 GiB) gives more headroom if needed. Only one instance is ever created -- there is no node-count variable in this design."
  type        = string
  default     = "g6e.2xlarge"
}

variable "root_volume_size_gb" {
  description = "Root EBS volume size (GiB), gp3, encrypted. Holds the OS, container image(s), vLLM install, and the Qwen3.6-27B FP8 checkpoint (~29 GiB) -- sized with comfortable headroom rather than a dedicated second data volume, since a single lab instance doesn't need the extra mount/format complexity of a separate EBS volume. See infra/README.md for the sizing rationale."
  type        = number
  default     = 300
}

variable "ssh_ingress_cidrs" {
  description = "CIDR blocks allowed to SSH (22/tcp) into the instance. Empty by default -- use AWS Systems Manager Session Manager instead (the instance role already has AmazonSSMManagedInstanceCore). Only set this if you specifically need direct SSH."
  type        = list(string)
  default     = []
}

variable "internal_ingress_cidrs" {
  description = "CIDR blocks allowed to reach vLLM/monitoring ports (see modules/networking for the port list). Defaults to the VPC CIDR only -- nothing is exposed to the public internet by default."
  type        = list(string)
  default     = null # resolved to [var.vpc_cidr] in main.tf when left null
}

variable "ssh_key_name" {
  description = "Optional existing EC2 key pair name to attach to the instance for SSH fallback. Leave null to rely solely on SSM Session Manager."
  type        = string
  default     = null
}

# --- HF_TOKEN secret slot ---------------------------------------------------
variable "hf_token_parameter_name" {
  description = "SSM Parameter Store path for the (empty at plan/apply time) HuggingFace token slot. The value is injected out-of-band by a human -- see infra/README.md. The Qwen lineup is Apache-2.0/ungated, so this is provisioned but expected to stay unused."
  type        = string
  default     = "/gpu-sizing-lab/hf-token"
}

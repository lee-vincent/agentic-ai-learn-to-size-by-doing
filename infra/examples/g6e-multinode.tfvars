# Alternate GPU profile: quota-constrained bring-up while the P-family
# vCPU quota increase (needed for the default 2x p5.48xlarge, see
# infra/README.md) is still pending review with AWS.
#
# Use case: this account's live quotas (verified 2026-07-15, us-east-1,
# `aws service-quotas get-service-quota`) are
#   - L-417A185B "Running On-Demand P instances": 64 vCPUs (can't fit even
#     one p5.48xlarge at 192 vCPU/node)
#   - L-DB2E81BA "Running On-Demand G and VT instances": 48 vCPUs
# 2x g6e.4xlarge is 2 x 16 = 32 vCPUs, which fits inside the 48-vCPU G&VT
# quota this account already has -- no quota increase needed to stand up a
# real multi-node cluster and validate Ray/vLLM/monitoring bring-up today.
#
# What you get, and what you give up, vs. the p5.48xlarge default: see
# infra/README.md, "Alternate G-family profile (quota-constrained
# bring-up)" section, for the full writeup (specs, pricing, EFA caveat, and
# which lineup models actually fit).
#
# Usage:
#   cd infra
#   terraform plan -var-file=examples/g6e-multinode.tfvars
#
# gpu_node_count stays >= 2 -- multi-node is a deliberate requirement for
# this lab (see CLAUDE.md/SPEC.md), not something to relax just because a
# single g6e.4xlarge's VRAM (44.7 GiB) would technically hold a small
# quantized model on its own.

# 1x NVIDIA L40S, 44.7 GiB VRAM, 16 vCPU, EfaSupported=false. Verified live
# via `aws ec2 describe-instance-types --instance-types g6e.4xlarge`,
# 2026-07-15: $3.00424/hr on-demand (us-east-1, Linux, shared tenancy) --
# ~$6.01/hr for the 2-node cluster below.
gpu_instance_type = "g6e.4xlarge"

# 2 x 16 = 32 vCPU total -- fits inside the 48-vCPU G&VT quota with
# headroom to spare (16 vCPU unused). Do NOT drop below 2: multi-node is in
# scope on purpose (see infra/variables.tf validation on gpu_node_count).
gpu_node_count = 2

# FSx/storage/networking/IAM/HF_TOKEN knobs are unchanged from
# terraform.tfvars.example -- only the GPU compute sizing differs for this
# profile. Override fsx_storage_capacity_gib below if you want to shrink it
# for this cheaper bring-up profile (2400 GiB default is sized for the
# flagship 397B-A17B model's quantized checkpoints, which do not fit this
# profile's VRAM anyway -- see README). Left at the default here so nothing
# else about the plan changes besides gpu_instance_type/gpu_node_count.

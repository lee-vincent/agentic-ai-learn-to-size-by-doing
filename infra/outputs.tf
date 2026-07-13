# --- Cluster facts ----------------------------------------------------------
output "gpu_node_public_ips" {
  value = module.compute.public_ips
}

output "gpu_node_private_ips" {
  value = module.compute.private_ips
}

output "gpu_cluster_total_vram_gib" {
  description = "Total GPU memory across the whole cluster, GiB (informational; see infra/README.md for per-precision headroom math)."
  value       = var.gpu_node_count * module.compute.gpu_count_per_node * (module.compute.gpu_memory_mib_each / 1024)
}

output "fsx_dns_name" {
  value = module.storage.fsx_dns_name
}

output "model_weights_bucket" {
  value = module.storage.bucket_name
}

# --- vCPU quota visibility --------------------------------------------------
output "quota_check" {
  description = "Current account vCPU quota for the relevant instance family vs. what this plan needs. See infra/scripts/check_gpu_quota.sh to request an increase."
  value = {
    instance_family     = local.applicable_quota_name
    current_quota_vcpus = local.applicable_quota_value
    required_vcpus      = local.required_vcpus
    sufficient          = local.quota_sufficient
  }
}

# --- HF_TOKEN injection reminder --------------------------------------------
output "hf_token_injection_command" {
  description = "Run this out-of-band, as a human, once infra is applied and if/when a gated model actually needs a token. Never run this from Terraform."
  value       = "aws ssm put-parameter --name '${var.hf_token_parameter_name}' --type SecureString --key-id '${module.iam.hf_token_kms_alias}' --value '<your real HF token>' --overwrite --region ${var.aws_region}"
}

# --- Cost estimate -----------------------------------------------------------
# See infra/README.md "Cost estimate" for the full breakdown, sources, and
# verification date (2026-07-13, AWS Price List API, us-east-1). Summarized
# here so it shows up in `terraform output` without reading the README.
output "estimated_hourly_cost_usd" {
  description = "Rough on-demand hourly cost estimate for this plan. Compute dominates; verify against infra/README.md before treating this as exact."
  value       = "See infra/README.md Cost Estimate section -- summary: gpu_node_count * on-demand rate for gpu_instance_type, plus ~$0.48/hr FSx + ~$0.01/hr EIPs. For the default (2x p5.48xlarge): $110.08/hr compute + ~$0.48/hr FSx (2400 GiB PERSISTENT_2 @125MB/s/TiB, $0.145/GB-mo / 730 hr) + $0.01/hr EIPs ~= $110.57/hr."
}

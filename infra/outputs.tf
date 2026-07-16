# --- Instance facts ----------------------------------------------------------
output "gpu_instance_id" {
  value = module.compute.instance_id
}

output "gpu_instance_public_ip" {
  value = module.compute.public_ip
}

output "gpu_instance_private_ip" {
  value = module.compute.private_ip
}

output "gpu_vram_gib" {
  description = "Total GPU memory on the instance, GiB (informational; see infra/README.md for the Qwen3.6-27B precision-vs-headroom math)."
  value       = module.compute.gpu_count * (module.compute.gpu_memory_mib / 1024)
}

# --- vCPU quota visibility --------------------------------------------------
output "quota_check" {
  description = "Current account vCPU quota for the relevant instance family vs. what this single instance needs. See infra/scripts/check_gpu_quota.sh to request an increase."
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
# verification date (2026-07-16, AWS Price List API, us-east-1). Summarized
# here so it shows up in `terraform output` without reading the README.
output "estimated_hourly_cost_usd" {
  description = "Rough on-demand hourly cost estimate for this plan. Compute dominates; verify against infra/README.md before treating this as exact."
  value       = "See infra/README.md Cost Estimate section -- summary for the default g6e.2xlarge: $2.24208/hr compute (verified 2026-07-16, AWS Price List API, us-east-1) + ~$0.033/hr EBS (300 GiB gp3 @ $0.08/GB-mo / 730 hr) + $0.005/hr public IPv4 ~= $2.28/hr (~$54.72/day if left running)."
}

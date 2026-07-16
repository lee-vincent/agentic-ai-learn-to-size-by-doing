locals {
  common_tags = {
    Project     = var.project
    Environment = var.environment
  }

  internal_ingress_cidrs = var.internal_ingress_cidrs == null ? [var.vpc_cidr] : var.internal_ingress_cidrs
}

module "networking" {
  source = "./modules/networking"

  project                = var.project
  vpc_cidr               = var.vpc_cidr
  subnet_cidr            = var.subnet_cidr
  gpu_instance_type      = var.gpu_instance_type
  ssh_ingress_cidrs      = var.ssh_ingress_cidrs
  internal_ingress_cidrs = local.internal_ingress_cidrs
  tags                   = local.common_tags
}

module "iam" {
  source = "./modules/iam"

  project                 = var.project
  hf_token_parameter_name = var.hf_token_parameter_name
  tags                    = local.common_tags
}

module "compute" {
  source = "./modules/compute"

  project               = var.project
  subnet_id             = module.networking.subnet_id
  security_group_id     = module.networking.security_group_id
  gpu_instance_type     = var.gpu_instance_type
  instance_profile_name = module.iam.instance_profile_name
  root_volume_size_gb   = var.root_volume_size_gb
  ssh_key_name          = var.ssh_key_name
  tags                  = local.common_tags
}

# --- vCPU quota visibility ---------------------------------------------
# Read-only check: surfaces the account's *current* vCPU quota for whichever
# instance family this single instance uses, next to what this plan actually
# needs (just this one instance's vCPU count), so the gap is visible in
# `terraform plan`/`terraform output` without a separate `aws service-quotas`
# call. Deliberately a data source, not the `aws_servicequotas_service_quota`
# *resource* -- requesting a quota increase is a business decision with
# human-timescale approval, not something that should happen automatically as
# a side effect of `terraform apply`. See infra/README.md and
# infra/scripts/check_gpu_quota.sh for the actual request step.
#
# The default gpu_instance_type (g6e.2xlarge) is G-family, but both quota
# codes are checked so switching gpu_instance_type to a P-family type later
# doesn't silently go unchecked.
data "aws_servicequotas_service_quota" "g_instances" {
  service_code = "ec2"
  quota_code   = "L-DB2E81BA" # "Running On-Demand G and VT instances" (vCPU-denominated)
}

data "aws_servicequotas_service_quota" "p_instances" {
  service_code = "ec2"
  quota_code   = "L-417A185B" # "Running On-Demand P instances" (vCPU-denominated)
}

locals {
  required_vcpus         = module.compute.vcpus
  gpu_is_g_family        = startswith(lower(var.gpu_instance_type), "g")
  applicable_quota_name  = local.gpu_is_g_family ? "Running On-Demand G and VT instances" : "Running On-Demand P instances"
  applicable_quota_value = local.gpu_is_g_family ? data.aws_servicequotas_service_quota.g_instances.value : data.aws_servicequotas_service_quota.p_instances.value
  quota_sufficient       = local.applicable_quota_value >= local.required_vcpus
}

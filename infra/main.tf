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
  cluster_subnet_cidr    = var.cluster_subnet_cidr
  gpu_instance_type      = var.gpu_instance_type
  ssh_ingress_cidrs      = var.ssh_ingress_cidrs
  internal_ingress_cidrs = local.internal_ingress_cidrs
  tags                   = local.common_tags
}

module "storage" {
  source = "./modules/storage"

  project                         = var.project
  subnet_id                       = module.networking.subnet_id
  fsx_security_group_id           = module.networking.fsx_security_group_id
  fsx_storage_capacity_gib        = var.fsx_storage_capacity_gib
  fsx_per_unit_storage_throughput = var.fsx_per_unit_storage_throughput
  tags                            = local.common_tags
}

module "iam" {
  source = "./modules/iam"

  project                 = var.project
  hf_token_parameter_name = var.hf_token_parameter_name
  weights_bucket_arn      = module.storage.bucket_arn
  tags                    = local.common_tags
}

module "compute" {
  source = "./modules/compute"

  project                   = var.project
  subnet_id                 = module.networking.subnet_id
  cluster_security_group_id = module.networking.cluster_security_group_id
  gpu_instance_type         = var.gpu_instance_type
  gpu_node_count            = var.gpu_node_count
  instance_profile_name     = module.iam.instance_profile_name
  root_volume_size_gb       = var.root_volume_size_gb
  ssh_key_name              = var.ssh_key_name
  fsx_dns_name              = module.storage.fsx_dns_name
  fsx_mount_name            = module.storage.fsx_mount_name
  tags                      = local.common_tags
}

# --- vCPU quota visibility ---------------------------------------------
# Read-only check: surfaces the account's *current* "Running On-Demand P
# instances" quota (vCPU-denominated) next to what this plan actually needs,
# so the gap is visible in `terraform plan`/`terraform output` without
# requiring a separate `aws service-quotas` call. Deliberately a data
# source, not the `aws_servicequotas_service_quota` *resource* -- requesting
# a quota increase is a business decision with human-timescale approval, not
# something that should happen automatically as a side effect of `terraform
# apply`. See infra/README.md and infra/scripts/check_gpu_quota.sh for the
# actual request step.
data "aws_servicequotas_service_quota" "p_instances" {
  service_code = "ec2"
  quota_code   = "L-417A185B" # "Running On-Demand P instances" (vCPU-denominated)
}

# Also surfaced for visibility in case gpu_instance_type is switched to a
# G-family type instead of the default P-family choice -- e.g. the
# quota-constrained bring-up profile in infra/examples/g6e-multinode.tfvars
# (2x g6e.4xlarge, 16 vCPU/node = 32 vCPU total, fits comfortably inside the
# 48-vCPU G&VT quota that most accounts already have, unlike the P-family
# quota which AWS ships at a much lower default and reviews manually).
data "aws_servicequotas_service_quota" "g_instances" {
  service_code = "ec2"
  quota_code   = "L-DB2E81BA" # "Running On-Demand G and VT instances" (vCPU-denominated)
}

locals {
  required_vcpus         = var.gpu_node_count * module.compute.vcpus_per_node
  gpu_is_g_family        = startswith(lower(var.gpu_instance_type), "g")
  applicable_quota_name  = local.gpu_is_g_family ? "Running On-Demand G and VT instances" : "Running On-Demand P instances"
  applicable_quota_value = local.gpu_is_g_family ? data.aws_servicequotas_service_quota.g_instances.value : data.aws_servicequotas_service_quota.p_instances.value
  quota_sufficient       = local.applicable_quota_value >= local.required_vcpus
}

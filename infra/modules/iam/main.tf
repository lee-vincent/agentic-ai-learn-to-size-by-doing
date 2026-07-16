# --- HF_TOKEN secret slot ---------------------------------------------------
# Empty placeholder only. Terraform creates the parameter and its dedicated
# KMS key, but never manages the *value* -- see the lifecycle block below.
# The Qwen lineup in SPEC.md is entirely Apache-2.0/ungated, so this may
# never actually be needed, but the slot exists so the instance can read a
# token later without an infra change.
#
# Chosen SSM SecureString over Secrets Manager: functionally equivalent for
# a single flat value with no rotation requirement, but SSM Standard-tier
# parameters have no per-parameter monthly fee, whereas Secrets Manager
# charges ~$0.40/secret/month regardless of use. Since this secret may sit
# empty/unused for the entire life of the project, avoiding a guaranteed
# recurring charge for a maybe-never-used value is the right default here.
resource "aws_kms_key" "hf_token" {
  description             = "Encrypts the HF_TOKEN SSM parameter for ${var.project}"
  enable_key_rotation     = true
  deletion_window_in_days = 30

  tags = merge(var.tags, { Name = "${var.project}-hf-token-kms" })
}

resource "aws_kms_alias" "hf_token" {
  name          = "alias/${var.project}-hf-token"
  target_key_id = aws_kms_key.hf_token.key_id
}

resource "aws_ssm_parameter" "hf_token" {
  name        = var.hf_token_parameter_name
  description = "HuggingFace token for gated model access. EMPTY placeholder -- injected out-of-band by a human, never by Terraform. See infra/README.md."
  type        = "SecureString"
  key_id      = aws_kms_key.hf_token.key_id

  # Placeholder string, NOT a real token. This is only what the parameter is
  # created with; real injection happens out-of-band (see README) via:
  #   aws ssm put-parameter --name "${var.hf_token_parameter_name}" \
  #     --type SecureString --key-id <this key's arn/alias> \
  #     --value "<real token>" --overwrite
  # `ignore_changes = [value]` below means Terraform will never notice or
  # revert that out-of-band write on a later plan/apply.
  value = "REPLACE_OUT_OF_BAND_VIA_AWS_CLI_NOT_TERRAFORM"

  tags = merge(var.tags, { Name = "${var.project}-hf-token" })

  lifecycle {
    ignore_changes = [value]
  }
}

# --- GPU instance role ---------------------------------------------------
data "aws_iam_policy_document" "assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "gpu_instance" {
  name               = "${var.project}-gpu-instance"
  assume_role_policy = data.aws_iam_policy_document.assume_role.json

  tags = merge(var.tags, { Name = "${var.project}-gpu-instance-role" })
}

# Session Manager access instead of open SSH (pairs with ssh_ingress_cidrs
# defaulting to empty in the networking module).
resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.gpu_instance.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

# Read-only ECR pull access, in case the vLLM container image
# (containers/vllm/, built in Phase 2) is ever hosted in ECR rather than
# built locally on the instance. Read-only, no push/delete/repo-management
# permissions -- harmless to keep provisioned even if Phase 2 ends up not
# using ECR at all.
resource "aws_iam_role_policy_attachment" "ecr_read" {
  role       = aws_iam_role.gpu_instance.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

# Least-privilege HF_TOKEN access: exactly this one parameter, exactly this
# one KMS key. Nothing broader -- no ssm:GetParameter* on other paths, no
# kms:Decrypt on other keys.
data "aws_iam_policy_document" "hf_token_read" {
  statement {
    sid       = "ReadHfTokenParameter"
    actions   = ["ssm:GetParameter"]
    resources = [aws_ssm_parameter.hf_token.arn]
  }

  statement {
    sid       = "DecryptHfTokenKey"
    actions   = ["kms:Decrypt", "kms:DescribeKey"]
    resources = [aws_kms_key.hf_token.arn]
  }
}

resource "aws_iam_policy" "hf_token_read" {
  name   = "${var.project}-hf-token-read"
  policy = data.aws_iam_policy_document.hf_token_read.json
}

resource "aws_iam_role_policy_attachment" "hf_token_read" {
  role       = aws_iam_role.gpu_instance.name
  policy_arn = aws_iam_policy.hf_token_read.arn
}

resource "aws_iam_instance_profile" "gpu_instance" {
  name = "${var.project}-gpu-instance"
  role = aws_iam_role.gpu_instance.name

  tags = merge(var.tags, { Name = "${var.project}-gpu-instance-profile" })
}

data "aws_caller_identity" "current" {}

# Staging bucket for model weights. The real source of truth for weights is
# the HuggingFace Hub; this bucket + the FSx Data Repository Association
# below let a human (or a future serving-builder script) `aws s3 sync` a
# quantized checkpoint here once and have FSx lazily hydrate it on first
# read across every node, instead of every node re-downloading from HF.
resource "aws_s3_bucket" "weights" {
  bucket = "${var.project}-model-weights-${data.aws_caller_identity.current.account_id}"

  tags = merge(var.tags, { Name = "${var.project}-model-weights" })
}

resource "aws_s3_bucket_public_access_block" "weights" {
  bucket = aws_s3_bucket.weights.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "weights" {
  bucket = aws_s3_bucket.weights.id

  rule {
    apply_server_side_encryption_by_default {
      # SSE-S3 (AES256), not SSE-KMS: this bucket only ever holds
      # already-public, Apache-licensed model weights, so there's no
      # confidentiality reason to pay per-request KMS charges here. The
      # HF_TOKEN secret (the one thing in this stack that's actually
      # sensitive) gets its own dedicated CMK in the iam module instead.
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "weights" {
  bucket = aws_s3_bucket.weights.id

  versioning_configuration {
    status = "Disabled" # this bucket is a re-populatable cache, not a source of truth
  }
}

# Shared model-weight cache. PERSISTENT_2 SSD is chosen over SCRATCH_2 for
# a ~3.5% cost premium ($0.145 vs $0.140 per GB-month at the cheapest
# throughput tier, verified via the AWS Price List API, us-east-1,
# 2026-07-13): SCRATCH filesystems have no replication and are described by
# AWS as "temporary storage... not intended for high durability", which is
# a bad fit for a filesystem that will hold hours of `aws s3 sync` /
# HuggingFace downloads of multi-hundred-GB checkpoints. PERSISTENT_2 also
# supports in-place throughput/capacity scaling without a restore-from-
# backup cycle.
#
# Default capacity (2400 GiB) is sized to hold the flagship model's FP8
# (~378 GiB) and INT4 (~220 GiB) checkpoints plus both smaller lineup models
# (~52 GiB and ~67 GiB BF16) concurrently, with headroom for the flagship
# BF16 checkpoint (~751 GiB) too if you want all precision variants cached
# at once. See infra/README.md for the byte-for-byte derivation.
resource "aws_fsx_lustre_file_system" "weights_cache" {
  subnet_ids                      = [var.subnet_id]
  security_group_ids              = [var.fsx_security_group_id]
  deployment_type                 = "PERSISTENT_2"
  storage_type                    = "SSD"
  per_unit_storage_throughput     = var.fsx_per_unit_storage_throughput
  storage_capacity                = var.fsx_storage_capacity_gib
  file_system_type_version        = "2.15"
  copy_tags_to_backups            = true
  automatic_backup_retention_days = 0 # cache, not source of truth -- skip backups to save cost

  tags = merge(var.tags, { Name = "${var.project}-weights-cache" })
}

# Lazily hydrate FSx from the S3 staging bucket: files show up in the FSx
# namespace immediately (metadata only) and their content is pulled from S3
# on first read, so a human doesn't have to pre-download the checkpoint onto
# the filesystem before nodes can start reading it.
resource "aws_fsx_data_repository_association" "weights" {
  file_system_id       = aws_fsx_lustre_file_system.weights_cache.id
  data_repository_path = "s3://${aws_s3_bucket.weights.id}"
  file_system_path     = "/models"

  s3 {
    auto_import_policy {
      events = ["NEW", "CHANGED", "DELETED"]
    }
  }

  delete_data_in_filesystem = false
}

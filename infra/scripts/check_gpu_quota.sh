#!/usr/bin/env bash
# Check (and optionally request an increase to) the EC2 vCPU quota that
# actually applies to whichever GPU instance family this single-instance lab
# is configured with -- most AWS accounts start at (or near) 0 for the G/P
# instance families, and this lab needs at least
# `vcpus_per(gpu_instance_type)` vCPUs of quota before `terraform apply` can
# actually launch the instance (terraform plan does not check this -- the
# EC2 RunInstances call at apply time is where an insufficient quota
# surfaces, as a launch failure).
#
# Generalized across instance families rather than hardwired to G: both the
# quota code AND the vCPU count are derived live, not hardcoded --
#   - vCPUs: `aws ec2 describe-instance-types` (VCpuInfo.DefaultVCpus) for
#     whatever --instance-type you pass -- the same data Terraform's
#     aws_ec2_instance_type data source reads (modules/compute/main.tf), so
#     this script and `terraform output quota_check` never disagree.
#   - quota code: inferred from the instance type's leading letter --
#     "p..." -> L-417A185B ("Running On-Demand P instances"), "g..."/"vt..."
#     -> L-DB2E81BA ("Running On-Demand G and VT instances"). Override with
#     --quota-code (or the legacy --g-family flag) if you ever point this
#     at a family outside P/G/VT.
#
# This script only ever *reads* the quota and, if you pass --request,
# submits an increase request via the Service Quotas API. It never calls
# terraform apply/destroy and never launches/terminates anything itself.
#
# Usage:
#   ./check_gpu_quota.sh                                    # default: 1x g6e.2xlarge
#   ./check_gpu_quota.sh --instance-type g6e.4xlarge         # a bigger single-instance option
#   ./check_gpu_quota.sh --request 16                        # request an increase to 16 vCPUs
#   ./check_gpu_quota.sh --region us-west-2                  # check a different region
#   ./check_gpu_quota.sh --quota-code L-DB2E81BA              # force a specific quota code
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
INSTANCE_TYPE="g6e.2xlarge"
INSTANCE_COUNT=1
REQUEST_VALUE=""
QUOTA_CODE_OVERRIDE=""
SERVICE_CODE="ec2"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --request)
      REQUEST_VALUE="$2"
      shift 2
      ;;
    --region)
      REGION="$2"
      shift 2
      ;;
    --instance-type)
      INSTANCE_TYPE="$2"
      shift 2
      ;;
    --instance-count)
      # Not a Terraform variable in this design (this stack only ever
      # creates one instance) -- kept as a manual planning knob in case you
      # want to check quota headroom for running more than one at once
      # (e.g. a second scratch instance) without changing the .tf files.
      INSTANCE_COUNT="$2"
      shift 2
      ;;
    --quota-code)
      # Escape hatch: force a specific quota code instead of inferring one
      # from the instance type's leading letter (e.g. for a family this
      # script doesn't already know how to classify).
      QUOTA_CODE_OVERRIDE="$2"
      shift 2
      ;;
    --g-family)
      # Kept for backwards compatibility; equivalent to
      # --quota-code L-DB2E81BA. Auto-detection from --instance-type makes
      # this unnecessary in normal use now (just pass --instance-type).
      QUOTA_CODE_OVERRIDE="L-DB2E81BA"
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

echo "Region: $REGION"
echo "Instance type: $INSTANCE_TYPE   Instance count: $INSTANCE_COUNT"
echo

# --- Derive vCPUs live -- same source Terraform's aws_ec2_instance_type
# data source reads, no hardcoded lookup table. (EFA is out of scope for
# this single-instance design -- g6e.2xlarge doesn't support it anyway --
# so it's not queried here.) ---
INSTANCE_INFO=$(aws ec2 describe-instance-types \
  --region "$REGION" \
  --instance-types "$INSTANCE_TYPE" \
  --query 'InstanceTypes[0].{VCpus:VCpuInfo.DefaultVCpus}' \
  --output json)

VCPUS_PER_INSTANCE=$(echo "$INSTANCE_INFO" | python3 -c 'import json,sys; print(json.load(sys.stdin)["VCpus"])')
REQUIRED_VCPUS=$(( VCPUS_PER_INSTANCE * INSTANCE_COUNT ))

echo "vCPUs/instance: $VCPUS_PER_INSTANCE"
echo "Required vCPUs for $INSTANCE_COUNT instance(s): $REQUIRED_VCPUS"
echo

# --- Infer the applicable quota code from the instance type's leading
# letter, unless explicitly overridden. ---
if [[ -n "$QUOTA_CODE_OVERRIDE" ]]; then
  QUOTA_CODE="$QUOTA_CODE_OVERRIDE"
else
  LEADING_LETTER=$(echo "$INSTANCE_TYPE" | tr '[:upper:]' '[:lower:]' | cut -c1)
  case "$LEADING_LETTER" in
    p)
      QUOTA_CODE="L-417A185B" # Running On-Demand P instances
      ;;
    g)
      QUOTA_CODE="L-DB2E81BA" # Running On-Demand G and VT instances
      ;;
    v)
      # vt1.* (Xilinx VU9P transcoding) shares the G&VT pool too.
      QUOTA_CODE="L-DB2E81BA"
      ;;
    *)
      echo "Could not infer a quota code for instance type '$INSTANCE_TYPE' (leading letter '$LEADING_LETTER'). Pass --quota-code explicitly." >&2
      exit 1
      ;;
  esac
fi

echo "Checking quota code $QUOTA_CODE (service: $SERVICE_CODE)..."
echo

CURRENT=$(aws service-quotas get-service-quota \
  --region "$REGION" \
  --service-code "$SERVICE_CODE" \
  --quota-code "$QUOTA_CODE" \
  --query 'Quota.Value' --output text)

QUOTA_NAME=$(aws service-quotas get-service-quota \
  --region "$REGION" \
  --service-code "$SERVICE_CODE" \
  --quota-code "$QUOTA_CODE" \
  --query 'Quota.QuotaName' --output text)

echo "Current value of \"$QUOTA_NAME\": $CURRENT vCPUs"
echo "Required for this plan ($INSTANCE_COUNT x $INSTANCE_TYPE): $REQUIRED_VCPUS vCPUs"

if python3 -c "import sys; sys.exit(0 if float('$CURRENT') >= $REQUIRED_VCPUS else 1)"; then
  echo "SUFFICIENT -- current quota already covers this plan."
else
  SHORTFALL=$(python3 -c "print(int($REQUIRED_VCPUS - float('$CURRENT')))")
  echo "INSUFFICIENT -- current quota is short by $SHORTFALL vCPUs."
fi

# Check for any pending increase requests so you don't file a duplicate.
echo
echo "Pending requests for this quota (if any):"
aws service-quotas list-requested-service-quota-change-history-by-quota \
  --region "$REGION" \
  --service-code "$SERVICE_CODE" \
  --quota-code "$QUOTA_CODE" \
  --query 'RequestedQuotas[?Status==`PENDING` || Status==`CASE_OPENED`].{Id:Id,DesiredValue:DesiredValue,Status:Status}' \
  --output table || true

if [[ -n "$REQUEST_VALUE" ]]; then
  echo
  echo "Requesting increase to $REQUEST_VALUE vCPUs..."
  aws service-quotas request-service-quota-increase \
    --region "$REGION" \
    --service-code "$SERVICE_CODE" \
    --quota-code "$QUOTA_CODE" \
    --desired-value "$REQUEST_VALUE"
  echo "Request submitted. P-family increases in particular are often reviewed manually by AWS and can take from hours to several business days -- track status with:"
  echo "  aws service-quotas list-requested-service-quota-change-history-by-quota --region $REGION --service-code $SERVICE_CODE --quota-code $QUOTA_CODE"
else
  echo
  echo "No --request value given; not submitting anything. To request an increase (e.g. to exactly $REQUIRED_VCPUS, or more for future headroom):"
  echo "  $0 --instance-type $INSTANCE_TYPE --instance-count $INSTANCE_COUNT --region $REGION --request $REQUIRED_VCPUS"
fi

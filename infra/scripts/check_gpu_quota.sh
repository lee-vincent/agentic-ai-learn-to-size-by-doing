#!/usr/bin/env bash
# Check (and optionally request an increase to) the EC2 "Running On-Demand
# P instances" vCPU quota -- most AWS accounts start at 0 for the
# G/P instance families, and this cluster needs `gpu_node_count *
# vcpus_per(gpu_instance_type)` vCPUs of quota before `terraform apply` can
# actually launch anything (terraform plan does not check this -- the
# EC2 RunInstances call at apply time is where an insufficient quota
# surfaces, as a launch failure).
#
# This script only ever *reads* the quota and, if you pass --request,
# submits an increase request via the Service Quotas API. It never calls
# terraform apply/destroy and never launches/terminates anything itself.
#
# Usage:
#   ./check_gpu_quota.sh                      # just show current vs needed
#   ./check_gpu_quota.sh --request 384         # request an increase to 384 vCPUs
#   ./check_gpu_quota.sh --region us-west-2    # check a different region
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
REQUEST_VALUE=""
QUOTA_CODE="L-417A185B"   # Running On-Demand P instances (vCPU-denominated)
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
    --g-family)
      # Use this if gpu_instance_type in variables.tf is a G-family type
      # (e.g. g6e.48xlarge) instead of the default P-family (p5/p4d/etc.).
      QUOTA_CODE="L-DB2E81BA" # Running On-Demand G and VT instances
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

echo "Region: $REGION"
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
  echo "Request submitted. Large P-family increases are often reviewed manually by AWS and can take from hours to several business days -- track status with:"
  echo "  aws service-quotas list-requested-service-quota-change-history-by-quota --region $REGION --service-code $SERVICE_CODE --quota-code $QUOTA_CODE"
else
  echo
  echo "No --request value given; not submitting anything. To request an increase:"
  echo "  $0 --region $REGION --request <desired vCPU total>"
fi

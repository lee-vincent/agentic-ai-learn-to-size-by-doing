#!/usr/bin/env bash
# SessionStart hook — prints currently-running instances so cost visibility happens
# automatically every session, per this project's guardrail: no hard spend cap, but
# no flying blind either. Requires AWS CLI credentials to be configured.

set -euo pipefail

echo "=== cost-banner: currently running instances ==="
aws ec2 describe-instances \
  --filters "Name=instance-state-name,Values=running" \
  --query "Reservations[].Instances[].[InstanceId,InstanceType,LaunchTime]" \
  --output table 2>/dev/null || echo "cost-banner: could not query EC2 — check AWS credentials/region."

echo "=== cost-banner: reminder ==="
echo "Cross-check current on-demand rates for the instance types above before assuming a cached"
echo "number is still accurate. Run 'terraform destroy' (with human confirmation, per"
echo "cost-guard.sh) when you're done with a session."

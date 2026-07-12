#!/usr/bin/env bash
# PostToolUse hook — tracks consecutive failures of cost-incurring commands for the
# cost-guard circuit breaker in cost-guard.sh (PreToolUse). Resets the counter on
# success, increments it on failure.
#
# IMPORTANT: field names for the command and exit code are inferred from common
# hook payload conventions — confirm against code.claude.com/docs before relying
# on this in a real session.

set -euo pipefail

STATE_DIR=".claude/state"
FAIL_COUNT_FILE="$STATE_DIR/cost-guard-failcount"
mkdir -p "$STATE_DIR"

payload="$(cat)"
command="$(echo "$payload" | jq -r '.tool_input.command // empty' 2>/dev/null || echo "")"
exit_code="$(echo "$payload" | jq -r '.tool_result.exit_code // 0' 2>/dev/null || echo "0")"

COST_PATTERNS='terraform apply|terraform destroy|aws ec2 run-instances|aws ec2 create-fleet|aws ec2 terminate-instances|eksctl create cluster'

if echo "$command" | grep -qE "$COST_PATTERNS"; then
  if [ "$exit_code" != "0" ]; then
    fail_count=0
    [ -f "$FAIL_COUNT_FILE" ] && fail_count="$(cat "$FAIL_COUNT_FILE")"
    echo $((fail_count + 1)) > "$FAIL_COUNT_FILE"
    echo "cost-guard-track: recorded a failure ($((fail_count + 1))/2) for a cost-incurring command." >&2
  else
    rm -f "$FAIL_COUNT_FILE"
  fi
fi

exit 0

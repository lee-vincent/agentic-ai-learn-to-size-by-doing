#!/usr/bin/env bash
# PreToolUse hook — gates cost-incurring / destructive AWS actions behind explicit
# human confirmation, and enforces a consecutive-failure circuit breaker.
#
# IMPORTANT: this assumes Claude Code passes the tool-call payload as JSON on stdin
# with a `.tool_input.command` field for Bash calls. Confirm the exact hook payload
# schema against the current hooks reference at code.claude.com/docs before relying
# on this — the field names here are a reasonable inference, not a guarantee.

set -euo pipefail

STATE_DIR=".claude/state"
FAIL_COUNT_FILE="$STATE_DIR/cost-guard-failcount"
CONFIRM_FLAG="$STATE_DIR/cost-action-confirmed"
MAX_CONSECUTIVE_FAILURES=2

mkdir -p "$STATE_DIR"

payload="$(cat)"
command="$(echo "$payload" | jq -r '.tool_input.command // empty' 2>/dev/null || echo "")"

# Patterns that incur real AWS cost or destroy resources.
COST_PATTERNS='terraform apply|terraform destroy|aws ec2 run-instances|aws ec2 create-fleet|aws ec2 terminate-instances|eksctl create cluster'

if echo "$command" | grep -qE "$COST_PATTERNS"; then
  fail_count=0
  [ -f "$FAIL_COUNT_FILE" ] && fail_count="$(cat "$FAIL_COUNT_FILE")"

  if [ "$fail_count" -ge "$MAX_CONSECUTIVE_FAILURES" ]; then
    echo "cost-guard: BLOCKED — this cost-incurring command has failed $fail_count times in a row. Stopping to avoid runaway AWS spend. Investigate, then run: rm $FAIL_COUNT_FILE" >&2
    exit 2
  fi

  if [ ! -f "$CONFIRM_FLAG" ]; then
    echo "cost-guard: BLOCKED — '$command' incurs real AWS cost or is destructive. Review the plan/impact yourself, then run: touch $CONFIRM_FLAG   ...and retry." >&2
    exit 2
  fi

  # One-shot confirmation — consumed here so the next cost action needs a fresh confirm.
  rm -f "$CONFIRM_FLAG"
fi

exit 0

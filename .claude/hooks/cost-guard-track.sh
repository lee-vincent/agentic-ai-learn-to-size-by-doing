#!/usr/bin/env bash
# PostToolUse + PostToolUseFailure hook (matcher: Bash) — maintains the consecutive-failure
# counter that cost-guard.sh (PreToolUse) reads for its circuit breaker.
#
# CORRECTNESS NOTE (verified against https://code.claude.com/docs/en/hooks, 2026-07):
#   - PostToolUse fires ONLY after a tool call SUCCEEDS.
#   - PostToolUseFailure fires after a tool call FAILS.
# Failure is detected by WHICH event fired, not by an exit code. (An earlier version read a
# non-existent .tool_result.exit_code on a single PostToolUse hook, which never sees failures,
# so the breaker never armed.) Wired to BOTH events; branches on the event name.
#
# jq-optional, same rationale as cost-guard.sh.

set -euo pipefail

STATE_DIR="${CLAUDE_PROJECT_DIR:-.}/.claude/state"
FAIL_COUNT_FILE="$STATE_DIR/cost-guard-failcount"
MAX_CONSECUTIVE_FAILURES=2
mkdir -p "$STATE_DIR"

payload="$(cat)"
COST_PATTERNS='terraform[[:space:]]+apply|terraform[[:space:]]+destroy|aws[[:space:]]+ec2[[:space:]]+run-instances|aws[[:space:]]+ec2[[:space:]]+create-fleet|aws[[:space:]]+ec2[[:space:]]+terminate-instances|eksctl[[:space:]]+create[[:space:]]+cluster'

if command -v jq >/dev/null 2>&1; then
  cmd="$(printf '%s' "$payload" | jq -r '.tool_input.command // empty' 2>/dev/null || true)"
  event="$(printf '%s' "$payload" | jq -r '.hook_event_name // empty' 2>/dev/null || true)"
else
  cmd=""
  event=""
fi
haystack="${cmd:-$payload}"

# Resolve the event from the raw payload if jq wasn't available.
if [ -z "$event" ]; then
  if printf '%s' "$payload" | grep -q '"hook_event_name"[[:space:]]*:[[:space:]]*"PostToolUseFailure"'; then
    event="PostToolUseFailure"
  elif printf '%s' "$payload" | grep -q '"hook_event_name"[[:space:]]*:[[:space:]]*"PostToolUse"'; then
    event="PostToolUse"
  fi
fi

if printf '%s' "$haystack" | grep -qE "$COST_PATTERNS"; then
  case "$event" in
    PostToolUseFailure)
      fail_count=0
      [ -f "$FAIL_COUNT_FILE" ] && fail_count="$(cat "$FAIL_COUNT_FILE")"
      echo $((fail_count + 1)) > "$FAIL_COUNT_FILE"
      echo "cost-guard: recorded failure $((fail_count + 1))/$MAX_CONSECUTIVE_FAILURES for a cost-incurring command; breaker trips at $MAX_CONSECUTIVE_FAILURES." >&2
      ;;
    PostToolUse)
      rm -f "$FAIL_COUNT_FILE"   # success resets the consecutive-failure counter
      ;;
  esac
fi

exit 0

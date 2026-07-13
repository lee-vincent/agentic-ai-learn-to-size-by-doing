#!/usr/bin/env bash
# PreToolUse hook (matcher: Bash) — gates cost-incurring / destructive AWS actions behind
# explicit human confirmation, and enforces a consecutive-failure circuit breaker.
#
# Schema verified against https://code.claude.com/docs/en/hooks (checked 2026-07):
#   - PreToolUse receives the tool payload as JSON on stdin; a Bash command is at
#     .tool_input.command. Exit code 2 blocks the call and shows stderr to Claude.
#
# Dependency note: uses jq when available for a clean command string, but falls back to
# matching the raw JSON payload with grep when jq is absent. This is deliberate — a guard
# that silently no-ops because jq isn't installed is worse than no guard. grep is POSIX and
# always present. Paths use $CLAUDE_PROJECT_DIR (exported into every hook env) so the guard
# survives Claude cd-ing into infra/ or a worktree.

set -euo pipefail

STATE_DIR="${CLAUDE_PROJECT_DIR:-.}/.claude/state"
FAIL_COUNT_FILE="$STATE_DIR/cost-guard-failcount"
CONFIRM_FLAG="$STATE_DIR/cost-action-confirmed"
MAX_CONSECUTIVE_FAILURES=2
mkdir -p "$STATE_DIR"

payload="$(cat)"

# Commands that incur real AWS cost or destroy resources. [[:space:]]+ tolerates extra spaces.
COST_PATTERNS='terraform[[:space:]]+apply|terraform[[:space:]]+destroy|aws[[:space:]]+ec2[[:space:]]+run-instances|aws[[:space:]]+ec2[[:space:]]+create-fleet|aws[[:space:]]+ec2[[:space:]]+terminate-instances|eksctl[[:space:]]+create[[:space:]]+cluster'

if command -v jq >/dev/null 2>&1; then
  cmd="$(printf '%s' "$payload" | jq -r '.tool_input.command // empty' 2>/dev/null || true)"
else
  cmd=""   # no jq: match against the raw payload instead (command text appears verbatim in it)
fi
haystack="${cmd:-$payload}"

if printf '%s' "$haystack" | grep -qE "$COST_PATTERNS"; then
  shown="${cmd:-the requested command}"
  fail_count=0
  [ -f "$FAIL_COUNT_FILE" ] && fail_count="$(cat "$FAIL_COUNT_FILE")"

  if [ "$fail_count" -ge "$MAX_CONSECUTIVE_FAILURES" ]; then
    echo "cost-guard: BLOCKED — a cost-incurring command has failed $fail_count times in a row. Stopping so a retry loop can't quietly rack up AWS charges. Investigate, then clear the breaker: rm \"$FAIL_COUNT_FILE\"" >&2
    exit 2
  fi

  if [ ! -f "$CONFIRM_FLAG" ]; then
    echo "cost-guard: BLOCKED — '$shown' incurs real AWS cost or is destructive, and is not an auto-mode action for this project. If you (the human) want this specific run, confirm it: touch \"$CONFIRM_FLAG\"  — then retry. The flag is consumed after one use." >&2
    exit 2
  fi

  rm -f "$CONFIRM_FLAG"   # one-shot: next cost action needs a fresh confirm
fi

exit 0

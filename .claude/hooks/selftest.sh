#!/usr/bin/env bash
# Behavioral self-test for the cost-guard hooks.
#
# Runs the guard (PreToolUse) and tracker (PostToolUse / PostToolUseFailure) through a set of
# simulated Claude Code hook payloads and checks the OBSERVABLE behavior — exit codes and
# circuit-breaker state — rather than trusting the code by reading it.
#
# State is redirected into a throwaway temp dir via $CLAUDE_PROJECT_DIR, so your real
# .claude/state/ (the live failure counter and apply-confirm flag) is never touched.
#
# Run with:  make hooks-selftest      (or:  bash .claude/hooks/selftest.sh)
# Exit code: 0 if all behavioral checks pass, 1 otherwise. A missing exec bit is a WARN,
# not a failure, since the logic test invokes the scripts via `bash` regardless.

set -uo pipefail   # deliberately NOT -e: we inspect the hooks' non-zero exit codes on purpose

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GUARD="$SCRIPT_DIR/cost-guard.sh"
TRACK="$SCRIPT_DIR/cost-guard-track.sh"
BANNER="$SCRIPT_DIR/cost-banner.sh"

# Isolate state. The hooks derive their state path from $CLAUDE_PROJECT_DIR.
TEST_HOME="$(mktemp -d)"
export CLAUDE_PROJECT_DIR="$TEST_HOME"
STATE_DIR="$TEST_HOME/.claude/state"
CONFIRM="$STATE_DIR/cost-action-confirmed"
FAILCOUNT="$STATE_DIR/cost-guard-failcount"
cleanup() { rm -rf "$TEST_HOME"; }
trap cleanup EXIT

# Colored output only when attached to a terminal.
if [ -t 1 ]; then G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; N=$'\033[0m'; else G=""; R=""; Y=""; N=""; fi
pass=0; fail=0; warns=0
ok()   { printf '  %sPASS%s %s\n' "$G" "$N" "$1"; pass=$((pass+1)); }
no()   { printf '  %sFAIL%s %s\n' "$R" "$N" "$1"; fail=$((fail+1)); }
warn() { printf '  %sWARN%s %s\n' "$Y" "$N" "$1"; warns=$((warns+1)); }

run_guard() { printf '%s' "$1" | bash "$GUARD" >/dev/null 2>&1; echo $?; }
run_track() { printf '%s' "$1" | bash "$TRACK" >/dev/null 2>&1; }
reset_state() { rm -rf "$STATE_DIR"; mkdir -p "$STATE_DIR"; }

# Owner-execute check that reads the actual mode bits. `test -x` is unreliable here because it
# returns true for root (uid 0) even with no execute bits set. Parses the 4th char of `ls -l`
# permissions (-rwx...): 'x' or 's' means the owner-execute bit is set. Portable across GNU/BSD.
exec_bit_set() {
  local perms c
  perms="$(ls -ld "$1" 2>/dev/null | awk 'NR==1{print $1}')"
  c="${perms:3:1}"
  [ "$c" = "x" ] || [ "$c" = "s" ]
}

# Simulated payloads (shapes verified against code.claude.com/docs).
PRE_BENIGN='{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"npm test"}}'
PRE_APPLY='{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"cd infra && terraform apply -auto-approve"}}'
PRE_APPLY2='{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"terraform apply"}}'
FAIL_APPLY='{"hook_event_name":"PostToolUseFailure","tool_name":"Bash","tool_input":{"command":"terraform apply"}}'
OK_APPLY='{"hook_event_name":"PostToolUse","tool_name":"Bash","tool_input":{"command":"terraform apply"}}'
FAIL_BENIGN='{"hook_event_name":"PostToolUseFailure","tool_name":"Bash","tool_input":{"command":"npm test"}}'

echo "cost-guard hooks self-test"
if command -v jq >/dev/null 2>&1; then echo "  (jq present — exercising the jq parse path)"
else echo "  (jq absent — exercising the grep fallback path)"; fi
echo

# Preliminary: exec bit. Claude Code invokes hooks via exec form, which needs +x. WARN only.
for f in "$GUARD" "$TRACK" "$BANNER"; do
  exec_bit_set "$f" || warn "exec bit missing on $(basename "$f") — Claude Code needs it. Fix: make hooks-enable"
done

# 1 — benign command passes through
reset_state
[ "$(run_guard "$PRE_BENIGN")" = "0" ] && ok "benign command allowed (exit 0)" \
  || no "benign command was not allowed"

# 2 — cost command with no confirmation is blocked
reset_state
[ "$(run_guard "$PRE_APPLY")" = "2" ] && ok "unconfirmed cost command blocked (exit 2)" \
  || no "unconfirmed cost command was not blocked"

# 3 — confirm, then the cost command is allowed and the flag is consumed
reset_state; touch "$CONFIRM"
rc="$(run_guard "$PRE_APPLY2")"
if [ "$rc" = "0" ] && [ ! -f "$CONFIRM" ]; then ok "confirm-then-allow, flag consumed after one use"
else no "confirm-then-allow failed (exit=$rc, flag still present=$([ -f "$CONFIRM" ] && echo yes || echo no))"; fi

# 4 — two failures arm the circuit breaker
reset_state
run_track "$FAIL_APPLY"; run_track "$FAIL_APPLY"
if [ -f "$FAILCOUNT" ] && [ "$(cat "$FAILCOUNT")" = "2" ]; then ok "breaker arms after 2 failures (failcount=2)"
else no "breaker did not arm (failcount=$([ -f "$FAILCOUNT" ] && cat "$FAILCOUNT" || echo missing))"; fi

# 5 — at the breaker limit, a cost command is hard-blocked even WITH a confirm flag
reset_state; echo 2 > "$FAILCOUNT"; touch "$CONFIRM"
[ "$(run_guard "$PRE_APPLY2")" = "2" ] && ok "hard-block at breaker limit even when confirmed" \
  || no "did not hard-block at breaker limit"

# 6 — a success resets the failure counter
reset_state; echo 1 > "$FAILCOUNT"
run_track "$OK_APPLY"
[ ! -f "$FAILCOUNT" ] && ok "success resets the failure counter" \
  || no "success did not reset the counter"

# 7 — a benign command's failure is ignored by the tracker (no false increment)
reset_state
run_track "$FAIL_BENIGN"
[ ! -f "$FAILCOUNT" ] && ok "benign command failure ignored by tracker" \
  || no "benign failure was wrongly counted"

echo
[ "$warns" -gt 0 ] && printf '%s%d warning(s).%s\n' "$Y" "$warns" "$N"
if [ "$fail" -eq 0 ]; then
  printf '%sAll %d behavioral checks passed.%s\n' "$G" "$pass" "$N"
  exit 0
else
  printf '%s%d passed, %d FAILED.%s\n' "$R" "$pass" "$fail" "$N"
  exit 1
fi

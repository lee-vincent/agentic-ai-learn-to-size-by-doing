# Running this as a loop (Claude Code + Sonnet 5)

## 1. One-time setup
- Drop `CLAUDE.md`, `SPEC.md`, `GOALS.md`, `.claude/agents/*.md`, `.claude/hooks/*.sh`, and
  `.claude/settings.json` into your project repo root, and add the included `.gitignore` (it keeps
  `.claude/state/`, tfstate, and secrets out of git — the confirm flag living in `.claude/state/`
  must never be committed).
- `chmod +x .claude/hooks/*.sh`
- Start Claude Code in the repo, then run `/hooks`. You should see the guard on **PreToolUse**,
  the tracker on **both PostToolUse and PostToolUseFailure**, and the banner on **SessionStart**.
  The PostToolUseFailure wiring is the important one: `PostToolUse` fires only on *success*, so
  failure tracking (which arms the cost circuit breaker) has to hang off `PostToolUseFailure` —
  an earlier draft that used only `PostToolUse` never armed the breaker at all.
- The hook payload field names (`.tool_input.command`, `.hook_event_name`) were verified against
  `code.claude.com/docs`, and the scripts fall back to matching the raw JSON payload with `grep`
  when `jq` isn't installed, so they don't silently no-op on a machine without `jq`. Installing
  `jq` is still nice for cleaner command strings in the block messages.
- Recommended: run `make hooks-selftest` to confirm the guard actually behaves in your
  environment — it drives the hooks through simulated payloads and checks that benign commands
  pass, unconfirmed cost commands are blocked, the confirm flag is consumed once, and the circuit
  breaker arms after 2 failures and resets on success. It uses a throwaway state dir, so it never
  touches your real `.claude/state/`. Re-run it after editing any hook. (`make hooks-enable` sets
  the exec bit the hooks need; a missing bit shows up as a WARN in the self-test.)

## 2. Suggested sequence
1. Run Phase 1 from `GOALS.md` in the main session (no worktree — infra is one shared thing, not
   independent per-module work).
2. Review the Terraform plan and cost estimate yourself. Then apply — two options:
   - **Safest (default): apply it yourself** in your own terminal (`terraform apply`). The
     cost-guard hook only governs commands *Claude* runs, so your own shell isn't gated and no
     confirm flag is needed. This keeps the spend decision entirely in your hands.
   - **If you'd rather have Claude run it:** `touch .claude/state/cost-action-confirmed`, then ask
     Claude to apply. The hook allows exactly that one apply and consumes the flag. Use this only
     when you actively want Claude in the apply loop.
   Either way, this step is intentionally not part of any autonomous loop — see Guardrails in
   `CLAUDE.md`.
3. Once infra is up, launch `serving-builder`, `agent-builder`, `loadgen-builder`, and
   `monitoring-builder` as parallel subagents (each already configured with `isolation: worktree`
   in its frontmatter), since they touch independent directories and won't collide. Feed each its
   corresponding phase goal from `GOALS.md`.
4. After each subagent reports it believes its phase is done, invoke `checker` against that
   phase's goal text before merging the worktree back. Don't skip this even when the builder
   sounds confident.
5. Run `experiment-cli-builder` last (Phase 5) — it depends on serving, loadgen, and monitoring
   all being live and checker-approved.

## 3. Why the checker is separate from every builder
This is the single most important habit in loop engineering: a builder subagent grading its own
homework will happily report "done" on a broken deploy, because it's the same reasoning that
produced the bug. A separate `checker` subagent, with no stake in the work looking finished,
catches what the builder won't notice about itself.

## 4. The guardrail that matters most for this project specifically
Most loop-engineering writeups worry about token spend and context bloat ("doom loops"). Here,
the sharper risk is that a loop retrying a failed multi-node Terraform apply keeps AWS charges
running whether or not the retries ever succeed — GPU instances that are already up don't stop
costing money just because a subsequent step is failing. That's why `terraform apply` and any
instance-launching command are hook-gated behind explicit confirmation, and why the circuit
breaker trips after 2 consecutive failures instead of letting the loop keep swinging at it.

## 5. If you keep this lab around for repeated use
Once Phases 1–5 are stable, a `/loop 30m` on just `experiment-cli-builder`'s output — re-running
sweeps with different knob combinations while you do other work — is a reasonable use of `/loop`.
The underlying infra isn't being recreated, just re-configured and re-measured, so the blast
radius of a bad iteration there is much smaller than during initial infra build. Don't extend the
same casualness to anything that touches `infra/`.

## Note on currency
Claude Code's loop/goal/hook feature set is moving fast — this whole practice ("loop engineering")
only got its name in June 2026. Confirm exact command syntax and hook payload schemas against
`code.claude.com/docs` before leaning on this in a real session; some specifics here may have
already drifted by the time you read it.

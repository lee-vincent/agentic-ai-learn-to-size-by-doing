# Running this as a loop (Claude Code + Sonnet 5)

## 1. One-time setup
- Drop `CLAUDE.md`, `SPEC.md`, `GOALS.md`, `.claude/agents/*.md`, `.claude/hooks/*.sh`, and
  `.claude/settings.json` into your project repo root.
- `chmod +x .claude/hooks/*.sh`
- Start Claude Code in the repo, then run `/hooks` — it should list `cost-guard`,
  `cost-guard-track`, and `cost-banner`. If the payload field names in the hook scripts don't
  match what your Claude Code version actually sends, fix those first (see the comments in each
  script) — a hook that silently no-ops is worse than no hook, because it looks like protection
  that isn't there.

## 2. Suggested sequence
1. Run Phase 1 from `GOALS.md` in the main session (no worktree — infra is one shared thing, not
   independent per-module work).
2. Review the Terraform plan and cost estimate yourself. When you're satisfied, `touch
   .claude/state/cost-action-confirmed` and run `terraform apply` yourself, outside the agent
   loop. This step is intentionally not automated — see Guardrails in `CLAUDE.md`.
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

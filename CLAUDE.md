# Project: GPU/HPC Sizing Lab — Loop-Engineered Build

## Objective
Deploy a real multi-node, multi-GPU serving stack (vLLM + NVIDIA NIM) and a real agentic AI
client on AWS EC2, generate synthetic load, and measure how CPU/RAM/GPU/VRAM utilization and
inference-quality metrics (ITL, TPS, latency-per-output-token, TAT) respond as hardware and
software knobs change. Full spec — metrics, knobs, deliverables — lives in `SPEC.md`. Don't
re-derive it; read it.

## Scope decisions — already made, do not re-ask
- Parallelism: multi-GPU, **multi-node**. Must exercise data, tensor, pipeline, AND expert
  parallel (expert parallel requires one MoE model in the model lineup).
- Serving frameworks: **vLLM and NVIDIA NIM**, both behind a common OpenAI-compatible interface,
  so the load generator and agent can target either interchangeably.
- Cost posture: no hard spend cap, no automatic cluster shutdown. But every cost-incurring or
  destructive action is gated behind explicit human confirmation (see Guardrails), and cost
  visibility is mandatory every session.

## Guardrails — non-negotiable, enforced by hooks, not just this file
1. Never run `terraform apply`, `terraform destroy`, or anything that launches/terminates EC2
   instances without explicit human confirmation in this session. These are not auto-mode
   actions — `infra-builder` only runs `init`/`validate`/`plan`.
2. The `cost-guard` hook enforces a circuit breaker: after 2 consecutive failures of the same
   cost-incurring command, it blocks further attempts until a human clears the failure state.
3. Every session start prints currently-running instances and their on-demand rate
   (`cost-banner` hook) so nothing runs invisibly.
4. Builders never declare their own phase "done." A separate `checker` subagent verifies every
   phase against the goal text in `GOALS.md`.

## Subagent map (see `.claude/agents/`)
| Subagent | Owns | Runs in worktree |
|---|---|---|
| `infra-builder` | `infra/` — Terraform: networking, compute, storage, IAM/secrets | yes |
| `serving-builder` | `containers/vllm/`, `containers/nim/` | yes |
| `agent-builder` | `agent/` — the custom tool-calling agent | yes |
| `loadgen-builder` | `loadgen/` — genai-perf configs + agent-driven load harness | yes |
| `monitoring-builder` | `monitoring/` — Prometheus/Grafana/DCGM/Node Exporter | yes |
| `experiment-cli-builder` | `experiment-cli/` — sweep CLI, results, plots | yes (build last) |
| `checker` | verifies every phase; never writes application code | no |

## How to actually run this
See `GOALS.md` for the five phase goals (feed each to `/goal`) and
`README-loop-engineering.md` for the operational sequence, worktree usage, and confirmation gates.

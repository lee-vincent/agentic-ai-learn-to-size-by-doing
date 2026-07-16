# Project: GPU Sizing Lab — Loop-Engineered Build

## Objective
Deploy a real single-GPU vLLM serving stack and a real agentic AI client on a single AWS EC2 L40S
instance, generate synthetic load, and measure how CPU/RAM/GPU/VRAM utilization and
inference-quality metrics (TTFT, ITL, TPS, latency-per-output-token, TAT, KV cache hit rate)
respond as software knobs change. Full spec — metrics, knobs, model, deliverables — lives in
`SPEC.md`. Don't re-derive it; read it. The immediate goal is to get Qwen3.6-27B serving on one
`g6e.2xlarge` fast.

## Scope decisions — already made, do not re-ask
- Single node, single GPU. **No** multi-node, multi-GPU, or parallelism (data/tensor/pipeline/
  expert) — that was an earlier, over-ambitious scope that kept hitting real EC2 GPU capacity
  limits. It is explicitly out of scope now; see the "Scope history" section of `SPEC.md` for how
  to add it back later.
- Serving framework: **vLLM only**, behind an OpenAI-compatible interface. NVIDIA NIM was in an
  earlier draft and has been dropped.
- Model: **Qwen3.6-27B only** (dense), served at FP8 on a single L40S. The two MoE models from the
  earlier lineup (Qwen3.5-35B-A3B, Qwen3.5-397B-A17B) are out of scope for now.
- Instance: a single **`g6e.2xlarge`** (1× L40S, 8 vCPU, 64 GiB RAM) — low cost and widely
  available. No FSx, EFA, or placement group.
- Cost posture: no hard spend cap, no automatic shutdown. But every cost-incurring or destructive
  action is gated behind explicit human confirmation (see Guardrails), and cost visibility is
  mandatory every session.

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
| `infra-builder` | `infra/` — Terraform: networking, one GPU instance, EBS, IAM/secrets | yes |
| `serving-builder` | `containers/vllm/` — one image serving Qwen3.6-27B on a single GPU | yes |
| `agent-builder` | `agent/` — the custom tool-calling agent | yes |
| `loadgen-builder` | `loadgen/` — genai-perf configs + agent-driven load harness | yes |
| `monitoring-builder` | `monitoring/` — Prometheus/Grafana/DCGM/Node Exporter | yes |
| `experiment-cli-builder` | `experiment-cli/` — sweep CLI, results, plots | yes (build last) |
| `checker` | verifies every phase; never writes application code | no |

## How to actually run this
See `GOALS.md` for the five phase goals (feed each to `/goal`) and
`README-loop-engineering.md` for the operational sequence, worktree usage, and confirmation gates.

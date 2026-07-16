# GOALS — feed each phase to `/goal` in order

Immediate objective: **get Qwen3.6-27B serving on a single `g6e.2xlarge` (L40S) instance.** Phases
1–2 are the critical path to that; 3–5 build the measurement loop on top. No multi-node, no
multi-GPU — see `SPEC.md`.

## Phase 1 — Infra planned and reviewed (no apply)
```
/goal Terraform under infra/ provisions a SINGLE g6e.2xlarge GPU instance: a VPC/subnet/security
group, the instance with a generously-sized EBS root volume for model weights, and an IAM instance
profile with SSM Session Manager access (plus an optional, empty HF_TOKEN SSM SecureString slot).
No FSx, no EFA, no cluster placement group, no multi-node. `terraform validate` passes and
`terraform plan` is clean. Do NOT run terraform apply. Report the plan summary and current
estimated hourly cost for human review.
```
Checked by: `checker` — runs `terraform validate`/`plan`, confirms exactly one GPU instance and no
multi-node machinery, confirms no `apply` was invoked, reports the plan summary + cost.

## Phase 2 — vLLM serving Qwen3.6-27B healthy (THE milestone)
```
/goal A vLLM container under containers/vllm/ builds and serves Qwen3.6-27B on the single L40S GPU
via an OpenAI-compatible /v1/chat/completions endpoint. Precision (FP8 default) and KV-cache
strategy are configurable via environment/config, not hardcoded. A curl request returns 200 with a
valid completion. The model fits in the L40S's 44.7 GiB VRAM at the chosen precision with room for
KV cache.
```
Checked by: `checker` — builds the container, runs it against Qwen3.6-27B, curls the endpoint,
validates the response schema, confirms VRAM headroom from vLLM's startup log / nvidia-smi.

## Phase 3 — Agent and load generation working end to end
```
/goal The custom agent under agent/ completes a multi-step tool-calling task end to end against the
vLLM endpoint. genai-perf runs a sweep against the raw endpoint and produces TTFT/ITL/TPS/latency
numbers. The agent-driven load harness runs N concurrent agent sessions and logs turnaround time
per session.
```
Checked by: `checker` — runs one agent task and one short genai-perf run, confirms well-formed,
non-empty output from both.

## Phase 4 — Monitoring stack scraping everything
```
/goal Prometheus shows every target (DCGM Exporter, Node Exporter, vLLM metrics) as "up". The
Grafana dashboard loads and displays live CPU/RAM, GPU/VRAM, and inference metrics (including KV
cache hit rate) panels without errors. Everything runs on the single node.
```
Checked by: `checker` — queries the Prometheus targets API and the Grafana dashboard API.

## Phase 5 — One full knob sweep completes
```
/goal The experiment CLI under experiment-cli/ runs one complete sweep across at least two values
of one knob (e.g. precision FP8 vs INT4, or concurrency 1 vs 8, or thinking-mode on vs off), and
the results file contains one correctly-tagged row per run with all SPEC.md metrics populated.
```
Checked by: `checker` — inspects the results file schema and row count against the sweep config.

---
Notes:
- Phases 3–4 can run in parallel (independent directories), each in its own worktree. Phase 5
  depends on 2–4 being live, so build it last.
- The whole point of the tightened scope is speed: Phase 2 (Qwen actually serving) is the goal to
  chase first. Don't reintroduce multi-node/multi-model complexity — that's explicitly out of
  scope in `SPEC.md` until the single-GPU lab is proven.

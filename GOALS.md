# GOALS — feed each phase to `/goal` in order

## Phase 1 — Infra planned and reviewed (no apply)
```
/goal Terraform modules for networking, multi-node GPU compute, shared storage, and IAM/secrets
exist under infra/. `terraform validate` passes with no errors, and `terraform plan` produces a
clean plan with no unexpected resource changes. Do NOT run terraform apply. Stop and report the
plan summary and current estimated hourly cost for human review.
```
Checked by: `checker` — runs `terraform validate` and `terraform plan`, confirms no `apply` was
invoked, reports the plan summary.

## Phase 2 — vLLM serving stack healthy (single node first is fine)
```
/goal A vLLM container under containers/vllm/ builds successfully and can serve each of the three
SPEC.md lineup models (Qwen3.6-27B, Qwen3.5-35B-A3B, Qwen3.5-397B-A17B) via the same image with
model ID and parallelism config passed in as parameters. Each exposes an OpenAI-compatible
/v1/chat/completions endpoint. A curl request against each returns 200 with a valid completion.
Precision and KV-cache strategy are configurable via environment/config, not hardcoded. It's fine
if the 397B-A17B model only comes up in a reduced-precision or reduced-parallelism configuration
at this phase — full multi-node placement is proven out in later phases.
```
Checked by: `checker` — builds the container, runs it against each of the three models in turn,
curls the endpoint, validates the response schema.

## Phase 3 — Agent and load generation working end to end
```
/goal The custom agent under agent/ completes a multi-step tool-calling task end to end against
the vLLM endpoint. genai-perf runs a sweep against the raw endpoint and produces TTFT/ITL/TPS/
latency numbers. The agent-driven load harness runs N concurrent agent sessions and logs
turnaround time per session.
```
Checked by: `checker` — runs one agent task and one short genai-perf run, confirms well-formed,
non-empty output from both.

## Phase 4 — Monitoring stack scraping everything
```
/goal Prometheus shows every target (DCGM Exporter, Node Exporter, vLLM metrics) as "up". The
Grafana dashboard loads and displays live CPU/RAM, GPU/VRAM, and inference metrics (including KV
cache hit rate) panels without errors.
```
Checked by: `checker` — queries the Prometheus targets API and the Grafana dashboard API.

## Phase 5 — One full knob sweep completes
```
/goal The experiment CLI under experiment-cli/ runs one complete sweep across at least two values
of one knob (e.g. precision FP8 vs INT4, or model Qwen3.6-27B vs Qwen3.5-35B-A3B), and the results
file contains one correctly-tagged row per run with all SPEC.md metrics populated.
```
Checked by: `checker` — inspects the results file schema and row count against the sweep config.

---
Reminder: Phases 2–4 can run in parallel, each in its own worktree, since they touch independent
directories. Phase 5 depends on 2–4 all being live, so build it last. Reminder for Phase 2/5:
getting Qwen3.5-397B-A17B fully placed across multiple nodes is itself a milestone worth treating
as its own mini-goal once the basics are proven on the smaller two models — don't let it block
early progress on the rest of the pipeline.

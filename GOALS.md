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

## Phase 2 — Serving stack healthy (single node first is fine)
```
/goal vLLM and NIM containers under containers/ build successfully. Both expose an
OpenAI-compatible /v1/chat/completions endpoint. A curl request against each returns 200 with a
valid completion. Precision, KV-cache strategy, and model are configurable via environment/config,
not hardcoded.
```
Checked by: `checker` — builds each container, runs it, curls the endpoint, validates the response
schema.

## Phase 3 — Agent and load generation working end to end
```
/goal The custom agent under agent/ completes a multi-step tool-calling task end to end against
either serving backend. genai-perf runs a sweep against the raw endpoint and produces ITL/TPS/
latency numbers. The agent-driven load harness runs N concurrent agent sessions and logs
turnaround time per session.
```
Checked by: `checker` — runs one agent task and one short genai-perf run, confirms well-formed,
non-empty output from both.

## Phase 4 — Monitoring stack scraping everything
```
/goal Prometheus shows every target (DCGM Exporter, Node Exporter, vLLM metrics, NIM metrics) as
"up". The Grafana dashboard loads and displays live CPU/RAM, GPU/VRAM, and inference metrics
panels without errors.
```
Checked by: `checker` — queries the Prometheus targets API and the Grafana dashboard API.

## Phase 5 — One full knob sweep completes
```
/goal The experiment CLI under experiment-cli/ runs one complete sweep across at least two values
of one knob (e.g. precision FP16 vs INT4), and the results file contains one correctly-tagged row
per run with all eight SPEC.md metrics populated.
```
Checked by: `checker` — inspects the results file schema and row count against the sweep config.

---
Reminder: Phases 2–4 can run in parallel, each in its own worktree, since they touch independent
directories. Phase 5 depends on 2–4 all being live, so build it last.

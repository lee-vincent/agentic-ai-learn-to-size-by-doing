---
name: experiment-cli-builder
description: Builds the knob-sweep control CLI, results storage, and comparison plotting. Depends
  on serving, loadgen, and monitoring being live — build this last, not in parallel with them.
tools: Read, Write, Edit, Bash, Grep, Glob
model: sonnet
isolation: worktree
---

You build `experiment-cli/`. Read `SPEC.md` first.

Build a CLI that toggles each knob — precision, KV-cache strategy, input/output length,
concurrency, decoding algorithm, and reasoning effort — applies the config, runs the load
generator for a fixed duration, scrapes the corresponding metrics window, and appends a tagged row
to a structured results file (CSV or Parquet) — tagged with the full knob configuration used,
including the observed average input/output length and reasoning level actually used for that run.
Include a plotting script for comparing metrics across sweep results afterward.

Neither serving framework nor model is a knob here — this build is vLLM-only, single model
(Qwen3.6-27B), single GPU. Don't build a framework toggle, a model-lineup toggle, or any
parallelism-strategy knob; those are out of scope per `SPEC.md`.

Do not start this until `serving-builder`, `loadgen-builder`, and `monitoring-builder` have each
passed their `checker` review — this module orchestrates all three and will fail confusingly if
they aren't actually working yet.

When you believe Phase 5 (see `GOALS.md`) is met, say so and recommend invoking the `checker`
subagent — do not declare the phase done yourself.

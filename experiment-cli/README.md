# experiment-cli — Phase 5 experiment control CLI

Phase 5 (see repo-root `SPEC.md` / `GOALS.md`): a CLI that toggles one knob at a time, applies the
configuration, runs the load generators for a fixed duration, scrapes the corresponding Prometheus
metrics window, and appends one fully-tagged row per run to a structured results file. This
orchestrates three things that already exist and are live — it does not reimplement any of them:

| Already-live piece | Used for |
|---|---|
| `loadgen/genai-perf/run_sweep.sh` | TTFT, ITL, TPS (per-user + aggregate), request latency, request throughput, observed avg input/output length |
| `loadgen/agent_harness/run_harness.py` | Turnaround Time (TAT), client/agent-layer measured |
| `monitoring/` (Prometheus at `:9090`) | CPU/RAM/GPU/VRAM utilization, `vllm:kv_cache_usage_perc`, KV cache hit rate |

This build is **vLLM-only, single model (Qwen3.6-27B), single GPU** — no framework toggle, no
model-lineup toggle, no parallelism-strategy knob (see repo-root `SPEC.md` "knobs" / `CLAUDE.md`
"Scope decisions"). This CLI **never restarts or reconfigures the vLLM server** — knobs that need a
restart (precision, KV-cache strategy, decoding algorithm) are documented extension points only
(see "Extension points" below), not runnable in this version.

## What's here

```
experiment-cli/
├── experiment_cli/
│   ├── cli.py                # argument parsing + sweep orchestration (run_one())
│   ├── config.py              # sweep config loading (JSON + a minimal YAML-lite) + defaults
│   ├── knobs.py                # the generic knob abstraction + the registered knobs
│   ├── backends.py             # RealBackend (subprocess/HTTP) vs. DryRunBackend (fixtures)
│   ├── runners.py              # subprocess invocations of run_sweep.sh / run_harness.py
│   ├── dry_run.py               # --dry-run fixture generators
│   ├── genai_perf_parser.py     # profile_export_genai_perf.csv -> GenaiPerfMetrics
│   ├── harness_parser.py         # summary.json -> HarnessMetrics
│   ├── prometheus_client.py       # /api/v1/query_range -> (avg, max) per PromQL expr
│   ├── results.py                  # the results-row schema + CSV/JSONL append
│   ├── report.py                    # `report` command: human-readable table
│   └── plot.py                       # `plot` command: per-metric PNG bar charts (matplotlib optional)
├── sweeps/
│   ├── concurrency_1_8.json     # the REQUIRED sweep (concurrency 1 vs 8), JSON format
│   └── reasoning_effort_off_high.yaml  # bonus knob example, YAML-lite format
├── tests/                        # unit tests + one offline dry-run end-to-end test (see below)
└── results/                      # default --results-dir (gitignored contents, see .gitkeep)
```

## Quickstart: offline dry run (no network, no GPU, no live stack needed)

```bash
cd experiment-cli
python3 -m experiment_cli sweep --config sweeps/concurrency_1_8.json --dry-run
python3 -m experiment_cli report --results-csv results/results.csv
python3 -m experiment_cli plot --results-csv results/results.csv --output-dir results/plots
```

`--dry-run` stubs the three externals (genai-perf, the agent harness, Prometheus) with fixture
files/payloads shaped exactly like the real thing (see `dry_run.py`) — but runs every parser and
the row-assembly logic for real, so it's a genuine mechanical test of the whole pipeline, not a
separate untested path. See "Verified locally vs. deferred" below for exactly what this does and
doesn't prove.

## Running a real sweep on the g6e.2xlarge instance

This CLI has no hardcoded paths or URLs — every tool path and base URL is a flag/env var, per the
build brief. Two ways to invoke it on the instance, once `serving-builder`/`loadgen-builder`/
`monitoring-builder` are all live (this module's whole reason to wait on them):

### Option A — directly on the host (simplest; matches how `loadgen/` itself documents running)

`loadgen/genai-perf/README.md`'s own documented setup installs `genai-perf` into a venv on the
host (PEP 668 blocks system-wide `pip install` there) rather than a container — there is no
pre-built `genai-perf` container image in this repo as of this build. Run this CLI the same way:

```bash
# one-time, from the repo root on the instance:
python3 -m venv .venv-genai-perf && source .venv-genai-perf/bin/activate && pip install genai-perf

# the real sweep (concurrency 1 vs 8 -- the REQUIRED Phase 5 sweep):
python3 -m experiment_cli sweep \
  --config experiment-cli/sweeps/concurrency_1_8.json \
  --results-dir experiment-cli/results \
  --genai-perf-script loadgen/genai-perf/run_sweep.sh \
  --harness-script loadgen/agent_harness/run_harness.py \
  --repo-root . \
  --agent-cwd /path/to/agent-worktree \
  --genai-perf-base-url http://localhost:8000 \
  --harness-base-url http://localhost:8000/v1 \
  --prometheus-url http://localhost:9090
```
(run from the repo root, with `experiment-cli/`, `loadgen/`, and the merged `agent/` package all
checked out side by side — same layout `loadgen/agent_harness/README.md`'s own real-sweep examples
assume.)

### Option B — inside a container with `--network host` (per the build brief's suggested pattern)

If instead you build/run this from inside a container that has `genai-perf` on `PATH` and a
checkout of `loadgen/`+`experiment-cli/` mounted (e.g. `python:3.11-slim` with `pip install
genai-perf` baked in, or whatever image `serving-builder`/`loadgen-builder` end up publishing —
none is pinned by this build, since it doesn't exist yet):

```bash
docker run --rm --network host \
  -v /path/to/repo/loadgen:/opt/loadgen-app/loadgen \
  -v /path/to/repo/experiment-cli:/opt/experiment-app/experiment-cli \
  -v /path/to/agent-worktree:/opt/agent-app/agent \
  -w /opt/experiment-app \
  <image-with-genai-perf-and-python3.11> \
  python3 -m experiment_cli sweep \
    --config experiment-cli/sweeps/concurrency_1_8.json \
    --results-dir experiment-cli/results \
    --genai-perf-script /opt/loadgen-app/loadgen/genai-perf/run_sweep.sh \
    --harness-script /opt/loadgen-app/loadgen/agent_harness/run_harness.py \
    --repo-root /opt/loadgen-app \
    --agent-cwd /opt/agent-app \
    --genai-perf-base-url http://localhost:8000 \
    --harness-base-url http://localhost:8000/v1 \
    --prometheus-url http://localhost:9090
```
`--network host` is required (matches `containers/vllm/` and `monitoring/`'s own compose files —
see `monitoring/README.md` "Networking") so `localhost:8000`/`:9090` inside the container actually
reach vLLM/Prometheus on the host's network namespace. **No docker-in-docker** — this CLI never
spawns another container itself; it only shells out to `run_sweep.sh` (which itself just calls the
`genai-perf` binary already on `PATH` in whatever process runs this CLI) and to
`run_harness.py` as a plain Python subprocess.

Either way, `python3 -m experiment_cli report --results-csv experiment-cli/results/results.csv`
afterward prints a human-readable table, and `python3 -m experiment_cli plot ...` emits PNGs if
`matplotlib` is importable in that environment (`pip install matplotlib` — optional, degrades
gracefully otherwise, see "Plotting" below).

## Sweep config format

JSON is the fully-supported, recommended format (`sweeps/concurrency_1_8.json`); a deliberately
minimal YAML-lite is also accepted (`sweeps/reasoning_effort_off_high.yaml` — see
`config.py`'s `_load_yaml_lite()` docstring for exactly what subset of YAML it handles: flat
`key: value`, one level of nesting via a trailing-colon `fixed:` block, `#` comments, and
JSON-syntax or bare-word scalars/lists as values). Required keys:

| Key | Required? | Meaning |
|---|---|---|
| `knob` | yes | One of `concurrency`, `isl_osl`, `reasoning_effort` (runnable); `precision`/`kv_cache_strategy`/`decoding_algorithm` are refused with a clear error — see "Extension points". |
| `values` | yes | A list with at least one value (the Phase 5 goal wants **at least two**); one sweep run per value. |
| `model` | no (default `Qwen/Qwen3.6-27B-FP8`) | Passed to both genai-perf and the harness. |
| `genai_perf_base_url` | no (default `http://localhost:8000`) | Bare `host:port`, no `/v1` — genai-perf's own convention. |
| `harness_base_url` | no (default `http://localhost:8000/v1`) | **Includes** `/v1` — the agent harness's own convention (see `loadgen/agent_harness/README.md` "gotcha"). |
| `prometheus_url` | no (default `http://localhost:9090`) | |
| `requests_per_concurrency`, `warmup_requests` | no (10 / 2) | Passed straight through to `run_sweep.sh`. |
| `harness_concurrency_cap` | no (default 16) | The agent harness is far more expensive per-request than genai-perf's raw completions, so its concurrency is `min(concurrency_knob_value, this_cap)`, not necessarily the same as genai-perf's. |
| `fixed` | no | The other five SPEC.md knob dimensions' values for THIS sweep (see `config.DEFAULT_FIXED`) — every row still tags all six dimensions regardless of which one is being swept. |

Every one of these is also overridable by a CLI flag (`python3 -m experiment_cli sweep --help`) or
an environment variable (`GENAI_PERF_BASE_URL`, `HARNESS_BASE_URL`, `PROMETHEUS_URL`,
`EXPERIMENT_CLI_MODEL`, `GENAI_PERF_SCRIPT`, `HARNESS_SCRIPT`, `AGENT_CWD`,
`EXPERIMENT_CLI_REPO_ROOT`, `HARNESS_ENGINE`, `HARNESS_PYTHON_BIN`) — **configuration precedence
is CLI flag > config file > environment variable > hardcoded localhost default.** No path is ever
hardcoded to `/opt/...` in the source; those are deploy-time choices made at invocation time (see
"Running a real sweep" above).

## Results schema

One row per run, appended to both `results/results.csv` and `results/results.jsonl` (identical
data; JSONL for easy programmatic re-loading). Full column list (`experiment_cli/results.py`
`COLUMNS`):

| Column(s) | Source | Notes |
|---|---|---|
| `run_id`, `timestamp` | this CLI | `timestamp` == `window_start_iso`. |
| `window_start_epoch`/`_iso`, `window_end_epoch`/`_iso` | this CLI | The exact wall-clock window the Prometheus query spans — recorded before genai-perf starts and after the harness finishes. |
| `knob_name`, `knob_value` | sweep config | The one knob actually being swept this invocation. |
| `model` | sweep config | |
| `precision`, `kv_cache_strategy`, `decoding_algorithm` | `fixed` config | **Not verified against the live server** in this CLI version — see "Extension points": these columns record what was *configured to be true* (documented vLLM defaults), not independently confirmed via a server query. |
| `concurrency`, `isl_mean_configured`, `osl_mean_configured`, `reasoning_effort_configured` | `fixed` config, knob-overridden | The full SPEC.md knob configuration used for this run — "tagged with the full knob configuration used" per the build brief, whether or not each one is the knob being swept. |
| `observed_input_len_avg`, `observed_output_len_avg` | genai-perf CSV | SPEC.md "Average input and output length (observed per run)" — genai-perf's own `Input/Output Sequence Length (tokens)` row averages, i.e. what was **actually** sent/generated, as opposed to the `*_configured` target. |
| `reasoning_effort_observed` | == `reasoning_effort_configured` | SPEC.md "Reasoning level / effort used (observed per run)" — see caveat in `results.py`'s docstring: neither vLLM nor the agent harness currently reports back a distinct "effort actually used" signal, so the best available proxy is what was requested. |
| `ttft_ms_{avg,p50,p90,p99}` | genai-perf CSV | Time To First Token. |
| `itl_ms_{avg,p50,p90,p99}` | genai-perf CSV | Inter-Token Latency. |
| `tps_per_user`, `tps_aggregate` | genai-perf CSV | Per-user (`Output Token Throughput Per User`, falls back to `1000/itl_ms_avg` on older genai-perf versions that lack that row) and aggregate (`Output Token Throughput`) tokens/sec. |
| `latency_per_output_token_ms` | == `itl_ms_avg` | SPEC.md defines this as total-generation-time / total-output-tokens — the same quantity ITL measures (time between consecutive output tokens); vLLM's own `vllm:request_time_per_output_token_seconds` histogram computes the identical thing server-side (see `monitoring/README.md`). **Documented equivalence, not a bug.** |
| `request_latency_ms_{avg,p50,p90,p99}` | genai-perf CSV | |
| `request_throughput_rps` | genai-perf CSV | |
| `tat_s_{avg,p50,p90,min,max}` | harness `summary.json` | Turnaround Time, measured client/agent-side per SPEC.md's explicit instruction. |
| `cpu_util_pct_{avg,max}` | Prometheus | `100 - avg(rate(node_cpu_seconds_total{mode="idle"}[1m]))*100`. |
| `ram_used_gib_{avg,max}` | Prometheus | `(MemTotal - MemAvailable) / 2^30`. |
| `gpu_util_pct_{avg,max}` | Prometheus | `DCGM_FI_DEV_GPU_UTIL`. |
| `vram_used_gib_{avg,max}` | Prometheus | `DCGM_FI_DEV_FB_USED / 1024`. |
| `kv_cache_usage_pct_{avg,max}` | Prometheus | `vllm:kv_cache_usage_perc * 100` (source metric is a 0-1 fraction; multiplied by 100 here so the column is a true 0-100 percentage, unlike the Grafana dashboard which instead uses a `percentunit` display unit on the raw fraction). |
| `kv_cache_hit_rate_{avg,max}` | Prometheus | `sum(rate(vllm:prefix_cache_hits_total[5m])) / sum(rate(vllm:prefix_cache_queries_total[5m]))` — **see the caveat below**, left as a raw 0-1 fraction (not `*100`). |
| `genai_perf_csv_path`, `genai_perf_manifest_path`, `harness_summary_path`, `harness_sessions_csv_path` | this CLI | Paths to the raw per-run artifacts, for drilling into a specific run later. |

Every numeric column is **coerced to `0.0`, never left `null`/empty**, if the underlying source is
missing/unparseable (see `results._num()`) — this is required behavior for the KV-hit-rate caveat
below, and applied uniformly so the CSV never has a hole in it.

### KV cache hit rate caveat (IMPORTANT — read before trusting a `0.0`)

**Known behavior on this stack**: Qwen3.6-27B is a hybrid Gated-DeltaNet architecture, and
`vllm:prefix_cache_hits_total` has been observed to stay at `0` even for duplicate prompts — so the
hit-rate ratio query can have **no matching series at all** (Prometheus returns an empty `result`
list, not a literal `0` — `0/0` is undefined in PromQL, so a ratio with no samples on either side
produces no data point, which is a structurally different thing than "the rate really is zero").
This CLI's `prometheus_client.reduce_avg_max()` turns that empty-result case into `(0.0, 0.0)` —
**required behavior, not a bug** — for every one of the six Prometheus-sourced metrics, not just
this one, since "no samples in this window" can in principle happen to any of them (e.g. querying
before vLLM has served its first request). **A `kv_cache_hit_rate_avg` of exactly `0.0` in a
results row is therefore ambiguous between "genuinely observed a 0% hit rate" and "no data" — check
`vllm:prefix_cache_hits_total`/`_queries_total` directly on the live Prometheus if this distinction
matters for a given run.** See `monitoring/README.md`'s own "Known uncertainties" for the same
caveat from the monitoring side.

## The generic knob abstraction + extension points

`experiment_cli/knobs.py` defines a `KnobSpec` per SPEC.md knob dimension. Three are fully
implemented (no vLLM server restart needed):

| Knob | Sweep value format | What it changes |
|---|---|---|
| `concurrency` | integer, e.g. `8` | genai-perf's `CONCURRENCY_LIST` (one value per run) and the harness's `--concurrency` (capped at `harness_concurrency_cap`). **This is the one the Phase 5 goal requires at minimum** — no server restart, so it's also the recommended real-sweep target. |
| `isl_osl` | `"isl:osl"`, e.g. `"2000:64"` | genai-perf's `ISL_MEAN`/`OSL_MEAN` and the harness's `--isl-mean`/`--osl-hint-mean`. |
| `reasoning_effort` | one of `off`/`low`/`medium`/`high` | The harness's `--reasoning-effort` **only** — genai-perf's raw completions endpoint (`run_sweep.sh`'s `ENDPOINT_TYPE=completions` default) bypasses the chat template/reasoning parser entirely (see that script's own header comment), so this knob's genai-perf-sourced columns (TTFT/ITL/TPS/request-latency) will **not** meaningfully differ between values — only `tat_s_*` reflects it. |

Adding a new knob of this shape is one `_apply_xxx()` function + one `KNOBS` registry entry in
`knobs.py` — no other file needs to change (`cli.py`'s `run_one()` reads `concurrency`/`isl_mean`/
`osl_mean`/`reasoning_effort` generically out of the `base` dict every knob's `apply()` updates).

**Not implemented — documented extension points only** (selecting one of these in a sweep config
is refused with a clear, immediate error, never a silent no-op or a misleading row):

| Knob | Why it's out of scope for this CLI version | How to add it back |
|---|---|---|
| `precision` (FP8 vs INT4/AWQ) | Requires restarting the vLLM container with a different checkpoint/`--quantization` flag. | Add a knob whose `apply()` doesn't touch `base` directly but instead calls out to a new `server_control.py` module that stops/reconfigures/health-checks the `containers/vllm/` compose project between values, then set `restart_required=False` once that's wired up and health-checked. Budget real wall-clock time per value (container restart + model load). |
| `kv_cache_strategy` (PagedAttention/prefix caching/chunked prefill/KV-cache quantization) | Same — different vLLM startup flags. | Same pattern as `precision`. |
| `decoding_algorithm` (greedy/parallel sampling/speculative decoding/beam search) | Speculative decoding needs `--speculative-config` at server startup (restart); greedy vs. parallel-sampling vs. beam-search are per-request sampling params in principle, but genai-perf's own CLI surface for controlling them per-run wasn't verified as part of this build (`run_sweep.sh` doesn't expose a sampling-params knob today) — see `loadgen/genai-perf/README.md`/`run_sweep.sh` before wiring this up. | For the restart-free subset (greedy/parallel-sampling/beam-search): extend `run_sweep.sh`'s `EXTRA_ARGS` escape hatch with genai-perf's sampling-param flags first, confirm it round-trips against a live server, then add an `apply()` here. For speculative decoding: same pattern as `precision`. |

## Plotting

```bash
python3 -m experiment_cli plot --results-csv results/results.csv --output-dir results/plots
```
Emits one PNG bar chart per metric in `plot.DEFAULT_METRICS`, comparing every row's `knob_value`.
If `matplotlib` isn't importable, this prints a clear message and returns exit code `0` — **it
never fails the sweep** (the `sweep` command doesn't call `plot` at all; run them separately).

## Testing

```bash
python3 -m unittest discover -s experiment-cli/tests -v
```
Covers: genai-perf CSV parsing (including the documented thousands-separator/quoting shape and the
chat-endpoint `N/A` failure mode), harness `summary.json` parsing (including the `p90=None` <2-sample
case), Prometheus response parsing (success/empty/error, including the empty-vector -> `0.0`
requirement), the knob abstraction (including refusing restart-required knobs), sweep-config
loading (JSON + YAML-lite), results-row assembly (every column present, never `None`), and one
offline end-to-end `--dry-run` sweep against the real checked-in `concurrency_1_8.json` config,
asserting exactly 2 rows with every column populated and the KV-hit-rate columns at exactly `0.0`.

## Verified locally vs. deferred to the live instance

**Verified for real, this build** (all offline — no network, no GPU, no docker):
- Every unit test above passes against fixtures, including a real genai-perf 0.0.16 CSV shape
  (`tests/fixtures/genai_perf_concurrency*.csv` — copied from an actual captured
  `loadgen-builder` validation run against a stub server, not invented).
- `python3 -m experiment_cli sweep --config sweeps/concurrency_1_8.json --dry-run` runs end to end,
  writes `results.csv`/`.jsonl` with exactly 2 rows (concurrency 1, 8), every column populated
  (verified by both an automated test and a manual run — see report to the orchestrating agent for
  the literal output).
- `report` and `plot` both run against that dry-run output; `plot` was verified both with
  matplotlib absent (graceful no-op, exit 0) and present in a throwaway venv (17 real PNGs written).
- Selecting `precision`/`kv_cache_strategy`/`decoding_algorithm` as the sweep knob is refused with a
  clear error and writes no `results.csv` — confirmed by test.

**NOT verified — genuinely requires the live g6e.2xlarge instance with vLLM +
loadgen/ + monitoring/ + agent/ all actually running**, per this build's own constraints (no
terraform/SSM/ssh, no reaching the instance, no network service — see the build brief):
- That `run_sweep.sh`'s real invocation produces a `profile_export_genai_perf.csv` at the exact
  path this CLI's `runners._find_genai_perf_csv()` expects (it has a glob-search fallback for
  exactly this reason, but the primary path convention is untested against a real run here).
- That `run_harness.py`'s real invocation against a real `agent/` produces a `summary.json` this
  CLI can parse end to end (the harness's own build already flagged `agent/`'s real interface as
  unverified against its own assumed contract — see `loadgen/agent_harness/README.md`).
- Any real Prometheus query against a live-loaded vLLM/DCGM/Node-Exporter stack (this build only
  exercises the parser against fixture JSON, never a real `/api/v1/query_range` call).
- Any actual Qwen3.6-27B TTFT/ITL/TPS/TAT/utilization number — every number in this README's
  dry-run demonstration is a synthetic, monotonic-in-concurrency fixture (see `dry_run.py`
  docstring), not a real benchmark result.

## Recommendation

Static/offline validation above is complete. Recommend invoking the `checker` subagent to verify
Phase 5 per `GOALS.md` — it can run this same `--dry-run` path to confirm the CLI mechanics, and
(per its own access) the real sweep against the live instance to confirm actual populated metrics.
Do not treat this as Phase 5 being declared done by this builder; that determination is
`checker`'s, per the project's guardrails.

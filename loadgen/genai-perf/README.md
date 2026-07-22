# loadgen/genai-perf — raw-endpoint knob-sweep benchmarking

Phase 3 (see repo-root `SPEC.md` / `GOALS.md`): NVIDIA `genai-perf` is the **tool of record** for
TTFT, ITL, TPS, and latency-per-output-token, run directly against vLLM's OpenAI-compatible
`/v1/chat/completions` endpoint (no agent in the loop — see `../agent_harness/` for that).

## What's here

| File | Purpose |
|---|---|
| `config.yaml` | Canonical genai-perf config (schema verified against the real installed tool — see "Verification" below). Checked-in source of truth for Qwen3.6-27B/vLLM defaults. |
| `run_sweep.sh` | Parameterized wrapper: loops `genai-perf profile` over a concurrency list, with model/endpoint/ISL/OSL all overridable via environment variables. **This is the recommended entrypoint** — `config.yaml` is the base config it overrides. |

## Setup

`genai-perf` is a real NVIDIA-published PyPI package (verified — see below), but this box's OS
blocks system-wide `pip install` (PEP 668). Use a venv:

```bash
python3 -m venv .venv-genai-perf
source .venv-genai-perf/bin/activate
pip install genai-perf
genai-perf --version   # confirm it's on PATH before running run_sweep.sh
```

The first real run against a Qwen3.6-27B endpoint will also download the model's **tokenizer**
(not weights — a few small files) from Hugging Face to accurately count input/output tokens.
`Qwen/Qwen3.6-27B-FP8` is not gated, so no `HF_TOKEN` is required for this.

## Knobs (SPEC.md: "average input and output length", "number of concurrent users")

All exposed as environment variables to `run_sweep.sh` (defaults shown):

| Variable | Default | Meaning |
|---|---|---|
| `MODEL_ID` | `Qwen/Qwen3.6-27B-FP8` | Model name knob — not hardcoded to one deployment |
| `SERVED_MODEL_NAME` | `$MODEL_ID` | What the server reports as `model` (only differs if vLLM's `--served-model-name` differs from the HF repo id) |
| `BASE_URL` | `http://localhost:8000` | Endpoint knob — bare host:port, **no** `/v1` suffix (genai-perf appends the path itself; contrast with `agent_harness`'s convention — see that README) |
| `ISL_MEAN` / `ISL_STDDEV` | `200` / `20` | Avg input length knob, in tokens (genai-perf generates synthetic prompts to hit this exactly, per its own tokenizer) |
| `OSL_MEAN` / `OSL_STDDEV` | `200` / `20` | Avg output length knob, in tokens |
| `CONCURRENCY_LIST` | `1,2,4,8,16` | Concurrent-user count knob — comma-separated list, one `genai-perf profile` run per value |
| `REQUESTS_PER_CONCURRENCY` | `10` | Requests measured per concurrency level (total request count = concurrency × this) |
| `WARMUP_REQUESTS` | `2` | Warmup requests before measurement starts, per level |
| `STREAMING` | `true` | Required for TTFT/ITL; set `false` for a TPS-only run |
| `TOKENIZER` / `TOKENIZER_TRUST_REMOTE_CODE` | `$MODEL_ID` / `false` | Tokenizer used for ISL/OSL accounting |
| `ARTIFACT_DIR` | `loadgen/results/genai-perf/<timestamp>` | Where results land |
| `EXTRA_ARGS` | *(empty)* | Escape hatch — any additional genai-perf flags, passed verbatim |

## Exact run commands

**Against the real vLLM endpoint** (run this on/near the g6e.2xlarge host, or with port 8000
tunneled — this dev box cannot reach it, see "Verified vs. deferred" below):

```bash
cd loadgen/genai-perf
source /path/to/.venv-genai-perf/bin/activate

# Quick default sweep (concurrency 1,2,4,8,16 at ISL=200/OSL=200):
BASE_URL=http://<vllm-host>:8000 ./run_sweep.sh

# A specific knob combination, e.g. long-context / short-answer at concurrency 8 only:
BASE_URL=http://<vllm-host>:8000 CONCURRENCY_LIST=8 ISL_MEAN=2000 OSL_MEAN=64 \
  ARTIFACT_DIR=loadgen/results/genai-perf/isl2000-osl64-c8 ./run_sweep.sh
```

**Or the single-config-file path** (equivalent, uses `genai-perf`'s own built-in concurrency
sweep instead of this repo's bash loop — uncomment the `analyze:` block in `config.yaml` first):

```bash
genai-perf config -f loadgen/genai-perf/config.yaml --override-config \
  -u http://<vllm-host>:8000 --synthetic-input-tokens-mean 500 --output-tokens-mean 300
```

## Expected output shape

Each `genai-perf profile` invocation (one per concurrency level via `run_sweep.sh`) writes to
`$ARTIFACT_DIR/concurrency<N>/<model>-openai-chat-concurrency<N>/`:

- `profile_export_genai_perf.csv` — the metrics table, e.g.:
  ```
  Metric,avg,min,max,p99,p95,p90,p75,p50,p25,p10,p5,p1
  Time To First Token (ms),...
  Time To Second Token (ms),...
  Request Latency (ms),...
  Inter Token Latency (ms),...
  Output Sequence Length (tokens),...
  Input Sequence Length (tokens),...

  Metric,Value
  Output Token Throughput (tokens/sec),...
  Request Throughput (per sec),...
  Request Count (count),...
  ```
  (a second block follows with per-GPU power/memory/utilization telemetry, populated via local
  NVML/DCGM if available on the host genai-perf runs from)
- `profile_export_genai_perf.json` — the same metrics as nested JSON (`time_to_first_token`,
  `inter_token_latency`, `request_latency`, `request_throughput`, `request_count`, each with
  `avg`/`p1`.../`p99`/`min`/`max`/`std`)
- `profile_export.json` — raw per-request perf_analyzer trace (what the two files above are computed from)
- `inputs.json` — the exact synthetic prompts sent

`run_sweep.sh` additionally writes `$ARTIFACT_DIR/manifest.json` recording the exact knob values
used for the whole sweep (model, url, ISL/OSL, concurrency list) — this is what
`experiment-cli` (Phase 5) should read to tag result rows.

## Verified locally vs. deferred to the live endpoint

This dev box cannot reach the vLLM endpoint (VPC-scoped, on the EC2 host's localhost:8000 —
confirmed unreachable, and this box also has no usable GPU to run vLLM itself). Nothing about
Qwen3.6-27B's real performance is asserted here.

**Verified for real, this session** (against a local stub OpenAI-compatible server —
`loadgen/scripts/stub_vllm_server.py`, run via `loadgen/scripts/run_local_validation.sh`):
- `genai-perf` is a real, installable PyPI package (`pip install genai-perf`, resolves to NVIDIA's
  actual GenAI-Perf 0.0.16, bundling the `perf_analyzer` binary) — not a name-alike.
- `config.yaml`'s YAML schema is not guessed: generated a real template via
  `genai-perf create-template` and cross-checked against `genai_perf/config/input/config_analyze.py`
  source to get the `analyze.concurrency.{start,stop,step}` sweep block's exact shape (an earlier
  draft with `analyze.sweep.type/range` was wrong and failed to parse — fixed after reading the
  real parser).
- `genai-perf config -f loadgen/genai-perf/config.yaml --override-config ...` parses this exact
  checked-in file and runs a full profile against a live HTTP endpoint (the stub), producing the
  documented CSV/JSON output.
- `run_sweep.sh` runs end to end: loops concurrency values, calls the tokenizer download path for
  a real HF-hosted tokenizer (`Qwen/Qwen3.6-27B-FP8` — confirmed to actually exist on Hugging Face
  via its API, not assumed), builds request/output-token distributions via `--synthetic-input-tokens-mean`/`--output-tokens-mean`, and produces one artifact directory + CSV/JSON per
  concurrency level plus a `manifest.json`.
- `genai-perf analyze --sweep-type concurrency --sweep-range 1:2` (the built-in sweep alternative
  mentioned above) was also run for real and produces a combined `analyze_export_genai_perf.csv`
  across all concurrency levels in one invocation.

**NOT verified — genuinely requires the live g6e.2xlarge + vLLM endpoint:**
- Any actual TTFT/ITL/TPS/latency-per-output-token number for Qwen3.6-27B. Every number produced
  during this build's validation runs came from a stub server that returns fixed placeholder text
  with ~0ms simulated compute — they are proof the *tool and config* work, not benchmark results.
  Do not reuse them for anything.
- Whether `--endpoint-type chat` + `--streaming` correctly parses vLLM 0.25.1's real SSE chunk
  format under load (the stub's SSE format was written to match the OpenAI spec vLLM implements,
  per `containers/vllm/README.md`'s confirmed route/behavior — but was not tested against the
  real vLLM process).
- GPU power/memory/utilization telemetry in the CSV's second block came from this dev box's own
  local NVML (an RTX-class card), not the target L40S — real numbers will differ substantially
  and this section is more properly DCGM Exporter's/Grafana's job per SPEC.md anyway.

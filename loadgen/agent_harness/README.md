# loadgen/agent_harness — agent-driven load harness

Phase 3 (see repo-root `SPEC.md` / `GOALS.md`): genai-perf (`../genai-perf/`) benchmarks raw
completions; it cannot reproduce **agentic-shaped traffic** — multi-turn tool-call loops, output
length driven by the model's own decisions rather than a fixed `max_tokens`, and burstier
concurrency. This harness runs N concurrent sessions of the Phase 3 `agent/` and logs turnaround
time (TAT) per session, per SPEC.md.

## Coordination status: `agent/` is not in this worktree

`agent/` is being built in a **parallel worktree** by `agent-builder` and is not present here (an
empty check at the start of this build confirmed it). Per the build brief, this harness codes
against a **documented, assumed interface** instead of blocking on the real package — see below.
Don't be alarmed that `import agent` fails from the repo root right now; that's expected until the
two worktrees are merged.

## What's here

| File | Purpose |
|---|---|
| `run_harness.py` | The harness CLI — orchestrates concurrency, generates/loads tasks, writes results. |
| `adapter.py` | Two engines (`cli`, `import`) that call the assumed `agent/` interface and normalize the result. **This is the reconciliation surface** — if the real `agent/` differs, this is the one file that needs to change. |
| `synthetic_tasks.py` | Generates synthetic task prompts at a configurable average input length, with a best-effort output-length hint. |
| `_selftest/fake_agent_pkg/agent/` | A minimal, black-box-faithful stand-in for `agent/` implementing just the assumed contract, backed by real HTTP calls — **self-test scaffolding only, delete once the real `agent/` lands.** Lets this harness be run and validated for real without the real agent or a live GPU. |

## Assumed `agent/` interface

This is the contract `adapter.py` codes against. It is a best-effort design based on the SPEC.md
brief for `agent/` (a real tool-calling agent, at minimum a calculator + retrieval tool, targeting
vLLM's OpenAI-compatible endpoint, with `base_url`/`model`/`reasoning_effort` configurable — not
hardcoded). **If the real `agent/` implementation differs, only `adapter.py` needs to change** —
`run_harness.py`, `synthetic_tasks.py`, and every CLI flag on `run_harness.py` are written against
`adapter.py`'s `NormalizedResult`, not the raw agent contract directly.

### `--engine cli` (default — most decoupled)

Invoke as a subprocess, one session per invocation:

```
python -m agent.cli --task "<task text>" --base-url <url> --model <model> \
  [--reasoning-effort {off,low,medium,high}] [--max-tokens N] --session-id <uuid>
```

Exit code `0` if the session's `status` was `"ok"`, `1` for any other terminal status (this
harness treats both as "the process ran to completion" and inspects the printed status; anything
else is a real crash). Prints exactly these lines to stdout, one session per invocation:

```
session_id:        <uuid>
status:            <ok|max_turns_exceeded|error> [(<error message>)]
tat_seconds:       <float>
num_model_calls:   <int>
num_tool_calls:    <int>
prompt_tokens:     <int>
completion_tokens: <int>
log_path:          <path or None>
final answer:      <text>
```

### `--engine import` (lower overhead at high concurrency)

```python
from agent.config import AgentConfig
from agent.loop import Agent

config = AgentConfig(base_url=..., model=..., reasoning_effort=..., max_tokens=..., request_timeout=...)
config.validate()
result = Agent(config).run_session(task, session_id=session_id)
# result.session_id, result.status, result.error, result.tat_seconds, result.num_model_calls,
# result.num_tool_calls, result.total_prompt_tokens, result.total_completion_tokens,
# result.final_text, result.log_path
```

### Gotcha: `--base-url` convention differs from `genai-perf`

The assumed `agent/` interface's `base_url` **includes the `/v1` suffix** (the OpenAI Python SDK
convention: `openai.OpenAI(base_url="http://host:8000/v1")`, which then appends
`/chat/completions` itself). `genai-perf`'s `--url` / `-u` is the **bare** `host:port` (it appends
its own path based on `--endpoint-type`). `run_harness.py`'s `--base-url` default is
`http://localhost:8000/v1`; `run_sweep.sh`'s `BASE_URL` default is `http://localhost:8000`. Don't
copy one into the other without adjusting.

### If the real interface differs at integration

1. Update `adapter.py`'s `CliEngine`/`ImportEngine` argument names, stdout parsing regex, and/or
   attribute names to match. `run_harness.py` and `synthetic_tasks.py` should not need changes
   unless the new interface exposes fundamentally different knobs.
2. Re-run `../scripts/run_local_validation.sh` (point `--agent-cwd` at the real `agent/`
   directory) to confirm the harness still drives it correctly before trusting any live-endpoint
   numbers.
3. Delete `_selftest/` once the real `agent/` is available and validated end to end — it's
   scaffolding, not a permanent fixture.

## Knobs (SPEC.md)

| Flag | Default | Meaning |
|---|---|---|
| `--base-url` | `http://localhost:8000/v1` | Endpoint knob (see gotcha above) |
| `--model` | `Qwen/Qwen3.6-27B-FP8` | Model name knob |
| `--reasoning-effort` | *(agent's own default)* | `off`\|`low`\|`medium`\|`high` passthrough |
| `--max-tokens` | *(agent's own default)* | Passthrough — caps output length per model call |
| `--concurrency` | `4` | Concurrent-user count knob — sessions in flight at once |
| `--num-sessions` | `concurrency * 5` | Total sessions across the run |
| `--arrival-pattern` | `closed` | `closed`: always exactly `--concurrency` in flight (matches genai-perf's own concurrency semantics). `poisson`: open-loop bursty arrivals at `--arrival-rate` sessions/sec, capped at `--concurrency` in flight — closer to SPEC.md's "burstier concurrency" language. |
| `--arrival-rate` | `1.0` | Sessions/sec mean for `--arrival-pattern poisson` |
| `--isl-mean` / `--isl-stddev` | `40` / `10` | Avg input length knob, in **words** (not tokens — see `synthetic_tasks.py` docstring for why) |
| `--osl-hint-mean` / `--osl-hint-stddev` | `150` / `50` | Avg output length knob — a best-effort instruction baked into the task text; actual output length is the agent's/model's to decide (that's the whole point of agentic-shaped traffic vs. genai-perf's fixed `--output-tokens-mean`) |
| `--tasks-file` | *(none)* | Override synthetic generation with real newline-delimited tasks |
| `--engine` | `cli` | `cli` \| `import` — see above |
| `--agent-cwd` | `.` | Directory containing the real `agent/` package |

## Exact run commands

**Against the real vLLM endpoint, once `agent/` exists** (run from wherever both `loadgen/` and
`agent/` are checked out — e.g. after merging worktrees):

```bash
python -m loadgen.agent_harness.run_harness \
  --base-url http://<vllm-host>:8000/v1 --model Qwen/Qwen3.6-27B-FP8 \
  --concurrency 8 --num-sessions 40 --isl-mean 60 --osl-hint-mean 200 \
  --agent-cwd /path/to/agent-worktree --engine cli
```

Sweep concurrency by re-running with different `--concurrency` values (each run writes its own
timestamped `--output-dir`, or pass one explicitly to tag it for `experiment-cli`):

```bash
for c in 1 2 4 8 16; do
  python -m loadgen.agent_harness.run_harness --concurrency "$c" --num-sessions $((c * 5)) \
    --base-url http://<vllm-host>:8000/v1 --agent-cwd /path/to/agent-worktree \
    --output-dir "loadgen/results/agent_harness/concurrency${c}"
done
```

**Local self-test (no live GPU, no real `agent/` needed)** — validates the harness itself:

```bash
python3 loadgen/scripts/stub_vllm_server.py --port 8098 &
python3 loadgen/agent_harness/run_harness.py \
  --base-url http://localhost:8098/v1 --model Qwen/Qwen3.6-27B-FP8 \
  --engine cli --agent-cwd loadgen/agent_harness/_selftest/fake_agent_pkg \
  --concurrency 4 --num-sessions 12 --isl-mean 30 --osl-hint-mean 80
```

or just run `../scripts/run_local_validation.sh`, which does this (both engines) plus the
genai-perf side, with cleanup.

## Expected output shape

Each invocation writes to `--output-dir` (default `loadgen/results/agent_harness/<UTC timestamp>/`):

- `sessions.csv` — one row per session: `session_id, status, error, tat_seconds,
  harness_observed_seconds, num_model_calls, num_tool_calls, prompt_tokens, completion_tokens,
  log_path, engine`. `tat_seconds` is the agent's own measurement (authoritative, per SPEC.md's
  "measure TAT at the client/agent layer"); `harness_observed_seconds` is this harness's wall-clock
  measurement around the call (includes subprocess spawn overhead for `--engine cli` — compare the
  two to see that overhead).
- `sessions.jsonl` — the same rows, full `NormalizedResult` fields (including `final_text`,
  `raw_stdout`/`raw_stderr` for `--engine cli` debugging), one JSON object per line.
- `summary.json` — run-level: knobs used, `num_sessions_completed`/`_ok`, `wall_clock_seconds`,
  `sessions_per_sec`, and `tat_seconds` percentiles (avg/min/max/p50/p90).

This is the shape `experiment-cli` (Phase 5) should read to tag one row per sweep point.

## Verified locally vs. deferred to the live endpoint + real `agent/`

**Verified for real, this session** (run `../scripts/run_local_validation.sh` to reproduce):
- `run_harness.py --engine cli` and `--engine import` both drive real concurrent sessions (via
  `ThreadPoolExecutor`) against a real HTTP server (`../scripts/stub_vllm_server.py`), using
  `_selftest/fake_agent_pkg/agent/` as a stand-in that implements exactly the assumed interface
  above (real subprocess spawn of `python -m agent.cli` for the `cli` engine; real `import
  agent.config`/`import agent.loop` for the `import` engine) — proving the adapter's subprocess
  invocation, stdout parsing, and in-process import/call path all work end to end, not just that
  they parse.
- `--arrival-pattern poisson` staggers session starts for real (measured wall-clock arrival span
  matches the configured `--arrival-rate` within the expected variance of a small sample).
- Missing/broken `agent/` fails loudly and distinctly: pointing `--agent-cwd` at a directory with
  no `agent` package raises `AgentInterfaceError` per session (caught, counted, reported with a
  clear message and a non-zero process exit code), rather than silently recording empty/garbage
  rows as if the run succeeded — confirmed by deliberately pointing `--engine import` at
  `/tmp/nonexistent-agent-dir`.
- `synthetic_tasks.generate_tasks()` produces prompts at the configured mean/stddev word-count
  target, mixed with arithmetic- and lookup-style questions per `--tool-mix`, and an
  output-length-hint sentence.
- `sessions.csv`/`.jsonl`/`summary.json` are all written with the documented schema and populated
  fields (checked by hand against real run output during this build).

**NOT verified — genuinely requires the real `agent/` and the live g6e.2xlarge + vLLM endpoint:**
- That the real `agent/` actually matches the assumed interface above byte-for-byte (this is
  exactly the reconciliation risk this README calls out — `adapter.py` is the file to revisit
  first if it doesn't).
- Any real TAT, tool-call-loop behavior, or output-length distribution from Qwen3.6-27B itself —
  every number in this build's validation runs came from the fake stub agent's instant, fixed-text
  responses, not real model inference.
- Real "burstier concurrency" behavior against an actually-loaded server (the stub has no queuing/
  backpressure, so concurrency effects on TAT are invisible here — that's the whole point of
  needing the live endpoint).

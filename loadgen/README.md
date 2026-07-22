# loadgen — Phase 3 load generation

Phase 3 (see repo-root `SPEC.md` / `GOALS.md`): two complementary load-generation paths against
the single Qwen3.6-27B-on-vLLM endpoint, per SPEC.md's "Load generation" architecture line.

| Path | Directory | Traffic shape | Metrics it's the tool of record for |
|---|---|---|---|
| Raw-endpoint benchmarking | `genai-perf/` | Single-turn completions, fixed synthetic ISL/OSL | TTFT, ITL, TPS, latency-per-output-token |
| Agent-driven harness | `agent_harness/` | Multi-turn, tool-call loops, model-decided output length, burstier concurrency | TAT per session (the thing genai-perf structurally cannot produce) |

Both are parameterized by model name, endpoint URL, average input/output length, and
concurrent-user count — SPEC.md's explicit knobs — via environment variables / CLI flags, not
hardcoded to one deployment. See each subdirectory's own README for exact flags and run commands.

```
loadgen/
├── genai-perf/            # raw-endpoint benchmarking (tool of record for TTFT/ITL/TPS/latency)
│   ├── config.yaml        #   checked-in base config (Qwen3.6-27B/vLLM defaults)
│   ├── run_sweep.sh        #   parameterized wrapper — recommended entrypoint
│   └── README.md
├── agent_harness/         # agent-driven harness (TAT under agentic traffic shape)
│   ├── run_harness.py     #   the harness CLI
│   ├── adapter.py         #   the agent/ integration surface — see README "Assumed interface"
│   ├── synthetic_tasks.py #   ISL/OSL-configurable synthetic task generator
│   ├── _selftest/         #   self-test-only stand-in for agent/ (delete once agent/ lands)
│   └── README.md
├── scripts/
│   ├── stub_vllm_server.py       # minimal OpenAI-compatible stub, local-validation only
│   └── run_local_validation.sh   # runs BOTH tools end to end against the stub
└── results/                # default output root for both tools (gitignored contents)
```

## Quickstart

```bash
# 1. One-time: install genai-perf into a venv (PEP 668 blocks system pip on this OS)
python3 -m venv .venv-genai-perf && source .venv-genai-perf/bin/activate && pip install genai-perf

# 2. Local mechanical validation (no live GPU, no real agent/ needed) -- proves both tools work
./loadgen/scripts/run_local_validation.sh

# 3. Against the real vLLM endpoint (once it's up on the g6e.2xlarge host -- see containers/vllm/):
BASE_URL=http://<vllm-host>:8000 ./loadgen/genai-perf/run_sweep.sh
python -m loadgen.agent_harness.run_harness --base-url http://<vllm-host>:8000/v1 \
  --agent-cwd /path/to/agent/  # once agent-builder's worktree is merged in
```

## Coordination note: `agent/` is not in this worktree

`agent_harness/` is built against a documented, assumed `agent/` CLI + importable-function
interface (see `agent_harness/README.md` "Assumed agent/ interface") because `agent/` is being
built concurrently in a separate worktree and was confirmed absent here at build time. This is
intentional per the build brief, not an oversight — reconcile `agent_harness/adapter.py` against
the real `agent/` once both land in the same tree, and re-run
`scripts/run_local_validation.sh` (pointed at the real `agent/`) before trusting live numbers.

## Verified locally vs. deferred to the live endpoint

This dev box **cannot reach the real vLLM endpoint** — it's VPC-scoped on a remote EC2 host's
localhost:8000 — and has no GPU capable of running Qwen3.6-27B itself (confirmed:
`nvidia-smi` on this box shows an RTX 1000 Ada Laptop GPU, 6 GiB VRAM). So no real
TTFT/ITL/TPS/latency/TAT numbers for Qwen3.6-27B exist anywhere in this build. What was
genuinely verified instead, all against a local stub OpenAI-compatible HTTP server
(`scripts/stub_vllm_server.py`) with real network I/O and real subprocess spawns (not mocked
function calls):

- `genai-perf` is a real, correctly-installable tool (not assumed) and this repo's
  `config.yaml`/`run_sweep.sh` drive it end to end, producing the documented CSV/JSON output
  shape — see `genai-perf/README.md` "Verified locally vs. deferred" for the full list.
- `agent_harness/run_harness.py` drives real concurrent sessions (both `--engine cli` and
  `--engine import`) against a stand-in that implements the assumed `agent/` contract, correctly
  logs TAT per session, handles both closed-loop and Poisson-arrival concurrency, and fails loudly
  (not silently) when the assumed interface isn't satisfiable — see `agent_harness/README.md`
  "Verified locally vs. deferred" for the full list.

**Deferred — needs the live g6e.2xlarge host, a running vLLM container, and (for
`agent_harness/`) the real `agent/` package merged in:**
- Any actual Qwen3.6-27B TTFT/ITL/TPS/latency-per-output-token/TAT number.
- Confirming `agent_harness/adapter.py`'s assumed interface actually matches the real `agent/`
  byte-for-byte.
- Real queuing/backpressure effects of concurrency on TAT (the stub has none).

## Recommendation

Static build-time checks above are complete for both tools; recommend invoking the `checker`
subagent to verify Phase 3 per `GOALS.md` (it can run the same local-validation path this build
used — `genai-perf` install + a short run, and one agent-harness run against a stub — since the
real endpoint isn't reachable from this environment either). Do not treat this as Phase 3 being
declared done by this builder; that determination is `checker`'s per the project's guardrails.

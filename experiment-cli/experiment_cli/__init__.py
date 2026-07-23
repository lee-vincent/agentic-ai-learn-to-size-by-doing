"""
experiment_cli — Phase 5 experiment control CLI (GPU Sizing Lab).

Orchestrates the three things that already exist and are live (see repo-root SPEC.md/GOALS.md):
  - loadgen/genai-perf/run_sweep.sh   -- raw-endpoint TTFT/ITL/TPS/latency benchmarking
  - loadgen/agent_harness/run_harness.py -- agent-driven TAT-per-session harness
  - monitoring/ (Prometheus at :9090)  -- CPU/RAM/GPU/VRAM + vLLM native metrics

For each value of one knob, this package runs both load tools once, scrapes the Prometheus
window that spans the run, and appends one tagged row to results/results.csv (+ .jsonl twin)
with every SPEC.md metric populated. See experiment-cli/README.md for the full contract.

This build is vLLM-only, single model (Qwen3.6-27B), single GPU -- no framework toggle, no
model-lineup toggle, no parallelism-strategy knob (see repo-root SPEC.md "knobs").
"""

__version__ = "0.1.0"

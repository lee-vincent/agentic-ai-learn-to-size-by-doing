"""
experiment_cli/results.py

The results-row schema (every SPEC.md metric + full knob tagging, per the Phase 5 goal) and the
CSV/JSONL append logic. One call to `build_row()` + `append_row()` per sweep run == one row.

Column groups (see README.md "Results schema" for the full table with units/sources):
  - Identity/window: run_id, timestamp, window_start_epoch/iso, window_end_epoch/iso
  - Knob tagging: knob_name, knob_value, model, and all SIX SPEC.md knob dimensions as they were
    actually configured for this run (precision, kv_cache_strategy, decoding_algorithm,
    concurrency, isl_mean_configured, osl_mean_configured, reasoning_effort_configured) --
    "tagged with the full knob configuration used" per the build brief, not just the one swept.
  - Observed (SPEC.md: "Average input and output length (observed per run)", "Reasoning level /
    effort used (observed per run)"): observed_input_len_avg / observed_output_len_avg (from
    genai-perf's own Input/Output Sequence Length columns -- what was ACTUALLY sent/generated, as
    opposed to the *_configured target), reasoning_effort_observed (see caveat below).
  - genai-perf-sourced: ttft_ms_*, itl_ms_*, tps_per_user, tps_aggregate,
    latency_per_output_token_ms, request_latency_ms_*, request_throughput_rps.
  - agent-harness-sourced: tat_s_*.
  - Prometheus-sourced: cpu_util_pct_*, ram_used_gib_*, gpu_util_pct_*, vram_used_gib_*,
    kv_cache_usage_pct_*, kv_cache_hit_rate_* (avg/max each, per the build brief).
  - Artifact paths: genai_perf_csv_path, genai_perf_manifest_path, harness_summary_path,
    harness_sessions_csv_path.

Caveat on reasoning_effort_observed: vLLM's response/metrics surface doesn't expose a distinct
"reasoning level actually used" signal independent of what was requested (no per-request
"effort" readback). The best available signal is exactly what was passed to the agent harness's
`--reasoning-effort` flag, so `reasoning_effort_observed` == `reasoning_effort_configured` here --
documented, not silently assumed. If a future agent/ version starts reporting the effort it
actually used (vs. requested) per session, wire that into harness_parser.py instead.

Numeric fields are NEVER left as Python None in a written row -- missing/unparseable inputs are
coerced to 0.0 by `_num()` below, matching the documented, required Prometheus-empty-vector
behavior (see prometheus_client.py), applied uniformly to every numeric column so the CSV never
has to represent a null.
"""
from __future__ import annotations

import csv
import json
import os
from typing import Any, Optional

COLUMNS: list[str] = [
    "run_id", "timestamp", "window_start_epoch", "window_start_iso",
    "window_end_epoch", "window_end_iso",
    "knob_name", "knob_value", "model",
    "precision", "kv_cache_strategy", "decoding_algorithm",
    "concurrency", "isl_mean_configured", "osl_mean_configured", "reasoning_effort_configured",
    "observed_input_len_avg", "observed_output_len_avg", "reasoning_effort_observed",
    "ttft_ms_avg", "ttft_ms_p50", "ttft_ms_p90", "ttft_ms_p99",
    "itl_ms_avg", "itl_ms_p50", "itl_ms_p90", "itl_ms_p99",
    "tps_per_user", "tps_aggregate",
    "latency_per_output_token_ms",
    "request_latency_ms_avg", "request_latency_ms_p50", "request_latency_ms_p90",
    "request_latency_ms_p99",
    "request_throughput_rps",
    "tat_s_avg", "tat_s_p50", "tat_s_p90", "tat_s_min", "tat_s_max",
    "cpu_util_pct_avg", "cpu_util_pct_max",
    "ram_used_gib_avg", "ram_used_gib_max",
    "gpu_util_pct_avg", "gpu_util_pct_max",
    "vram_used_gib_avg", "vram_used_gib_max",
    "kv_cache_usage_pct_avg", "kv_cache_usage_pct_max",
    "kv_cache_hit_rate_avg", "kv_cache_hit_rate_max",
    "genai_perf_csv_path", "genai_perf_manifest_path",
    "harness_summary_path", "harness_sessions_csv_path",
]

def _num(value: Optional[float]) -> float:
    """None/unparseable -> 0.0 (never left null) -- see module docstring."""
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def build_row(
    *,
    run_id: str,
    window_start_epoch: float,
    window_start_iso: str,
    window_end_epoch: float,
    window_end_iso: str,
    knob_name: str,
    knob_value: Any,
    model: str,
    base: dict,
    gp_metrics,   # genai_perf_parser.GenaiPerfMetrics
    h_metrics,    # harness_parser.HarnessMetrics
    prom_metrics: dict,  # {"<key>_avg": float, "<key>_max": float, ...} -- see prometheus_client.PROM_EXPRS
    gp_csv_path: str,
    gp_manifest_path: str,
    h_summary_path: str,
    h_sessions_csv_path: str,
) -> dict:
    reasoning_effort_configured = base.get("reasoning_effort")
    row = {
        "run_id": run_id,
        "timestamp": window_start_iso,
        "window_start_epoch": window_start_epoch,
        "window_start_iso": window_start_iso,
        "window_end_epoch": window_end_epoch,
        "window_end_iso": window_end_iso,
        "knob_name": knob_name,
        "knob_value": knob_value,
        "model": model,
        "precision": base.get("precision"),
        "kv_cache_strategy": base.get("kv_cache_strategy"),
        "decoding_algorithm": base.get("decoding_algorithm"),
        "concurrency": _num(base.get("concurrency")),
        "isl_mean_configured": _num(base.get("isl_mean")),
        "osl_mean_configured": _num(base.get("osl_mean")),
        "reasoning_effort_configured": reasoning_effort_configured,
        "observed_input_len_avg": _num(gp_metrics.observed_input_len_avg),
        "observed_output_len_avg": _num(gp_metrics.observed_output_len_avg),
        # See module docstring "Caveat on reasoning_effort_observed".
        "reasoning_effort_observed": reasoning_effort_configured,
        "ttft_ms_avg": _num(gp_metrics.ttft_ms_avg),
        "ttft_ms_p50": _num(gp_metrics.ttft_ms_p50),
        "ttft_ms_p90": _num(gp_metrics.ttft_ms_p90),
        "ttft_ms_p99": _num(gp_metrics.ttft_ms_p99),
        "itl_ms_avg": _num(gp_metrics.itl_ms_avg),
        "itl_ms_p50": _num(gp_metrics.itl_ms_p50),
        "itl_ms_p90": _num(gp_metrics.itl_ms_p90),
        "itl_ms_p99": _num(gp_metrics.itl_ms_p99),
        "tps_per_user": _num(gp_metrics.tps_per_user),
        "tps_aggregate": _num(gp_metrics.tps_aggregate),
        # SPEC.md lists both TPS/ITL and "latency per output token" as separate metrics but
        # defines the latter as total-generation-time / total-output-tokens -- exactly ITL's own
        # definition (time between consecutive output tokens); vLLM's own
        # vllm:request_time_per_output_token_seconds histogram computes the identical quantity
        # server-side (see monitoring/README.md). Documented equivalence, not a bug: this column
        # is intentionally == itl_ms_avg.
        "latency_per_output_token_ms": _num(gp_metrics.latency_per_output_token_ms),
        "request_latency_ms_avg": _num(gp_metrics.request_latency_ms_avg),
        "request_latency_ms_p50": _num(gp_metrics.request_latency_ms_p50),
        "request_latency_ms_p90": _num(gp_metrics.request_latency_ms_p90),
        "request_latency_ms_p99": _num(gp_metrics.request_latency_ms_p99),
        "request_throughput_rps": _num(gp_metrics.request_throughput_rps),
        "tat_s_avg": _num(h_metrics.tat_s_avg),
        "tat_s_p50": _num(h_metrics.tat_s_p50),
        "tat_s_p90": _num(h_metrics.tat_s_p90),
        "tat_s_min": _num(h_metrics.tat_s_min),
        "tat_s_max": _num(h_metrics.tat_s_max),
        "cpu_util_pct_avg": _num(prom_metrics.get("cpu_util_pct_avg")),
        "cpu_util_pct_max": _num(prom_metrics.get("cpu_util_pct_max")),
        "ram_used_gib_avg": _num(prom_metrics.get("ram_used_gib_avg")),
        "ram_used_gib_max": _num(prom_metrics.get("ram_used_gib_max")),
        "gpu_util_pct_avg": _num(prom_metrics.get("gpu_util_pct_avg")),
        "gpu_util_pct_max": _num(prom_metrics.get("gpu_util_pct_max")),
        "vram_used_gib_avg": _num(prom_metrics.get("vram_used_gib_avg")),
        "vram_used_gib_max": _num(prom_metrics.get("vram_used_gib_max")),
        "kv_cache_usage_pct_avg": _num(prom_metrics.get("kv_cache_usage_pct_avg")),
        "kv_cache_usage_pct_max": _num(prom_metrics.get("kv_cache_usage_pct_max")),
        "kv_cache_hit_rate_avg": _num(prom_metrics.get("kv_cache_hit_rate_avg")),
        "kv_cache_hit_rate_max": _num(prom_metrics.get("kv_cache_hit_rate_max")),
        "genai_perf_csv_path": gp_csv_path,
        "genai_perf_manifest_path": gp_manifest_path,
        "harness_summary_path": h_summary_path,
        "harness_sessions_csv_path": h_sessions_csv_path,
    }
    missing = [c for c in COLUMNS if c not in row]
    if missing:
        raise AssertionError(f"build_row() is missing required column(s): {missing}")
    return row


def append_row(csv_path: str, jsonl_path: str, row: dict) -> None:
    """Appends one row to both results.csv and its JSONL twin, writing the CSV header only if the
    file doesn't already exist (so re-running a sweep against the same results dir accumulates,
    matching genai-perf's/the harness's own append-friendly artifact-dir convention)."""
    os.makedirs(os.path.dirname(csv_path) or ".", exist_ok=True)
    os.makedirs(os.path.dirname(jsonl_path) or ".", exist_ok=True)

    write_header = not os.path.isfile(csv_path) or os.path.getsize(csv_path) == 0
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        if write_header:
            w.writeheader()
        w.writerow({k: row.get(k) for k in COLUMNS})

    with open(jsonl_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def read_rows(csv_path: str) -> list[dict]:
    with open(csv_path, "r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))

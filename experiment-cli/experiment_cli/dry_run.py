"""
experiment_cli/dry_run.py

Fixture generators for `--dry-run` (see backends.py). These stand in for the three externals this
CLI orchestrates -- genai-perf, the agent harness, and Prometheus -- entirely offline, no network,
no subprocess, no live GPU. Every fixture is shaped exactly like the real thing (the genai-perf CSV
shape below was copied from an actual genai-perf 0.0.16 run captured during loadgen-builder's own
local validation, not invented) so that the SAME parsers (genai_perf_parser.py, harness_parser.py,
prometheus_client.py) run in dry-run mode as in a real sweep -- dry-run only fakes the I/O layer,
never the parsing logic. This is what makes `--dry-run` a meaningful mechanical test of the whole
pipeline rather than a separate, unverified code path.

Numbers scale mildly with `concurrency` (higher concurrency -> higher TTFT/ITL/TAT/CPU/GPU/VRAM,
lower per-user throughput, higher aggregate throughput) purely so a 2-row dry-run sweep visibly
"looks like" a real sweep result -- these are NOT real Qwen3.6-27B numbers, see README.md.
"""
from __future__ import annotations

import csv
import json
import os


def write_fixture_genai_perf_csv(run_dir: str, concurrency: int, isl_mean: float, osl_mean: float,
                                  served_model_name: str, endpoint_type: str = "completions") -> str:
    """Writes a profile_export_genai_perf.csv at the exact path run_sweep.sh would use, and
    returns its path. Shape matches a real captured genai-perf 0.0.16 run (see module docstring)."""
    safe_name = served_model_name.replace("/", "_")
    concurrency_dir = os.path.join(run_dir, f"concurrency{concurrency}")
    profile_dir = os.path.join(
        concurrency_dir, f"{safe_name}-openai-{endpoint_type}-concurrency{concurrency}",
    )
    os.makedirs(profile_dir, exist_ok=True)
    csv_path = os.path.join(profile_dir, "profile_export_genai_perf.csv")

    # Mild, monotonic-in-concurrency synthetic numbers -- see module docstring.
    ttft_avg = 45.0 + 2.5 * concurrency
    itl_avg = 8.0 + 0.6 * concurrency
    req_lat_avg = ttft_avg + itl_avg * osl_mean
    tps_per_user = 1000.0 / itl_avg
    tps_aggregate = tps_per_user * concurrency * 0.92  # <1x linear: some contention modeled
    request_throughput = (1000.0 / req_lat_avg) * concurrency * 0.92

    def pct_row(name, avg, unit_scale=1.0):
        # avg/min/max/p99/p95/p90/p75/p50/p25/p10/p5/p1 -- fabricated but monotonic percentiles.
        return [name] + [f"{avg * f:.2f}" for f in
                          (1.0, 0.72, 1.55, 1.5, 1.4, 1.3, 1.12, 1.0, 0.9, 0.82, 0.78, 0.74)]

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Metric", "avg", "min", "max", "p99", "p95", "p90", "p75", "p50", "p25",
                    "p10", "p5", "p1"])
        w.writerow(pct_row("Time To First Token (ms)", ttft_avg))
        w.writerow(pct_row("Request Latency (ms)", req_lat_avg))
        w.writerow(pct_row("Inter Token Latency (ms)", itl_avg))
        w.writerow(pct_row("Output Token Throughput Per User (tokens/sec/user)", tps_per_user))
        w.writerow(["Output Sequence Length (tokens)"] + [f"{osl_mean:.2f}"] * 12)
        w.writerow(["Input Sequence Length (tokens)"] + [f"{isl_mean:.2f}"] * 12)
        w.writerow([])
        w.writerow(["Metric", "Value"])
        w.writerow(["Output Token Throughput (tokens/sec)", f"{tps_aggregate:.2f}"])
        w.writerow(["Request Throughput (per sec)", f"{request_throughput:.2f}"])
        w.writerow(["Request Count (count)", f"{concurrency * 10:.2f}"])

    manifest_path = os.path.join(run_dir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump({
            "tool": "genai-perf",
            "dry_run": True,
            "concurrency_list": str(concurrency),
            "isl_mean": isl_mean,
            "osl_mean": osl_mean,
            "artifact_dir": run_dir,
        }, f, indent=2)

    return csv_path


def write_fixture_harness_summary(output_dir: str, concurrency: int, num_sessions: int,
                                   base_url: str, model: str) -> str:
    """Writes summary.json + sessions.csv at the paths run_harness.py would use."""
    os.makedirs(output_dir, exist_ok=True)

    tat_avg = 2.0 + 0.18 * concurrency
    summary = {
        "base_url": base_url,
        "model": model,
        "engine": "cli",
        "concurrency": concurrency,
        "dry_run": True,
        "num_sessions_requested": num_sessions,
        "num_sessions_completed": num_sessions,
        "num_sessions_ok": num_sessions,
        "arrival_pattern": "closed",
        "wall_clock_seconds": tat_avg * num_sessions / max(concurrency, 1),
        "sessions_per_sec": concurrency / tat_avg,
        "tat_seconds": {
            "count": num_sessions,
            "avg": tat_avg,
            "min": tat_avg * 0.6,
            "max": tat_avg * 1.8,
            "p50": tat_avg * 0.95,
            "p90": tat_avg * 1.5,
        },
    }
    summary_path = os.path.join(output_dir, "summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    sessions_csv_path = os.path.join(output_dir, "sessions.csv")
    fieldnames = ["session_id", "status", "error", "tat_seconds", "harness_observed_seconds",
                  "num_model_calls", "num_tool_calls", "prompt_tokens", "completion_tokens",
                  "log_path", "engine"]
    with open(sessions_csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for i in range(num_sessions):
            w.writerow({
                "session_id": f"dry-run-{i}", "status": "ok", "error": "",
                "tat_seconds": f"{tat_avg:.3f}", "harness_observed_seconds": f"{tat_avg * 1.02:.3f}",
                "num_model_calls": 2, "num_tool_calls": 1, "prompt_tokens": 60,
                "completion_tokens": 150, "log_path": "", "engine": "cli",
            })

    return summary_path


def fixture_prometheus_payload(expr: str, concurrency: int) -> dict:
    """Returns a raw /api/v1/query_range-shaped payload for one PromQL expression, so the SAME
    prometheus_client.parse_query_range_response() runs against it as against the real thing.

    The KV-cache-hit-rate expression deliberately returns an EMPTY result list here -- exercising,
    in the dry run itself, the exact documented empty-vector-> 0.0 behavior this build is known to
    hit on the real Qwen3.6-27B endpoint (see README.md 'KV cache hit rate caveat')."""
    if "prefix_cache_hits_total" in expr:
        return {"status": "success", "data": {"resultType": "matrix", "result": []}}

    base_values = {
        "node_cpu_seconds_total": 20.0 + 6.0 * concurrency,          # -> cpu_util_pct
        "MemAvailable_bytes": 12.0 + 0.5 * concurrency,                # -> ram_used_gib
        "DCGM_FI_DEV_GPU_UTIL": min(95.0, 15.0 + 9.0 * concurrency),   # -> gpu_util_pct
        "DCGM_FI_DEV_FB_USED": 30.0 + 0.3 * concurrency,               # -> vram_used_gib
        "kv_cache_usage_perc": min(95.0, 5.0 + 4.0 * concurrency),     # -> kv_cache_usage_pct
    }
    value = 1.0
    for key, v in base_values.items():
        if key in expr:
            value = v
            break

    # 5 samples across a synthetic window, +/-5% jitter (deterministic, no randomness needed).
    start_ts = 1_700_000_000
    values = [[start_ts + i * 5, f"{value * (0.97 + 0.015 * i):.4f}"] for i in range(5)]
    return {
        "status": "success",
        "data": {"resultType": "matrix", "result": [{"metric": {}, "values": values}]},
    }

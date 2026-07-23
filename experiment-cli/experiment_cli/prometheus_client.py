"""
experiment_cli/prometheus_client.py

Queries Prometheus's HTTP API (`/api/v1/query_range`) over the exact wall-clock window one sweep
run took, for each of the six PromQL expressions in PROM_EXPRS (all copied verbatim from
monitoring/grafana/provisioning/dashboards/gpu-sizing-lab.json / monitoring/README.md -- these are
the checked, working queries for this exact stack, not re-derived here).

IMPORTANT KNOWN BEHAVIOR (per this build's brief, and monitoring/README.md's own "Known
uncertainties"): on Qwen3.6-27B (a hybrid Gated-DeltaNet architecture) `vllm:prefix_cache_hits_total`
has been observed to stay 0 even for duplicate prompts, so the KV-hit-rate expression's `result`
list can come back empty (Prometheus has literally no series to divide, not a zero -- `0/0` is
undefined and PromQL returns no data, not `0`, when either side of a ratio has no samples). This
module's `reduce_avg_max()` turns an EMPTY result list into `(0.0, 0.0)` -- never `None`, never a
crash -- for every metric, not just KV hit rate, since the same "no samples in this window" shape
can in principle happen to any of the six queries (e.g. querying before vLLM has served its first
request). See README.md "KV cache hit rate caveat" for the full explanation.
"""
from __future__ import annotations

import json
import math
import statistics
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

# PromQL expressions, copied verbatim from
# monitoring/grafana/provisioning/dashboards/gpu-sizing-lab.json.
#   - cpu_util_pct / ram_used_gib: Node Exporter (CPU Utilization / Memory Used panels)
#   - gpu_util_pct / vram_used_gib: DCGM Exporter (GPU Utilization / VRAM Used panels)
#   - kv_cache_usage_pct: vLLM's own vllm:kv_cache_usage_perc gauge (0-1 fraction in source;
#     multiplied by 100 here so the results-row column is a true 0-100 percentage, matching its
#     `_pct` column name -- the dashboard instead uses Grafana's `percentunit` unit to display the
#     raw 0-1 fraction as a percent, which this CLI's plain-CSV output can't do, hence the *100).
#   - kv_cache_hit_rate: vLLM's native prefix-cache hit rate (see module docstring above) -- left
#     as a raw 0-1 fraction (not *100), matching its `_rate` column name.
PROM_EXPRS: dict[str, str] = {
    "cpu_util_pct": '100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[1m])) * 100)',
    "ram_used_gib": "(node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes) / 1073741824",
    "gpu_util_pct": "DCGM_FI_DEV_GPU_UTIL",
    "vram_used_gib": "DCGM_FI_DEV_FB_USED / 1024",
    "kv_cache_usage_pct": "vllm:kv_cache_usage_perc * 100",
    "kv_cache_hit_rate": (
        "sum(rate(vllm:prefix_cache_hits_total[5m])) / "
        "sum(rate(vllm:prefix_cache_queries_total[5m]))"
    ),
}


class PrometheusError(Exception):
    """Raised for a real Prometheus-side error (bad query, unreachable server) -- distinct from
    the documented empty-vector case, which is not an error (see reduce_avg_max())."""


def query_range(base_url: str, expr: str, start: float, end: float, step: int = 5,
                 timeout: float = 10.0) -> dict:
    """Calls GET {base_url}/api/v1/query_range and returns the raw decoded JSON payload (dict).
    Raises PrometheusError on a connection failure or a non-'success' status -- callers decide
    whether that's fatal (see cli.py --allow-prometheus-failures)."""
    query = urllib.parse.urlencode({
        "query": expr,
        "start": str(start),
        "end": str(end),
        "step": str(step),
    })
    url = f"{base_url.rstrip('/')}/api/v1/query_range?{query}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            payload = json.load(resp)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise PrometheusError(f"could not reach Prometheus at {base_url!r} for query {expr!r}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise PrometheusError(f"Prometheus at {base_url!r} returned non-JSON for query {expr!r}: {exc}") from exc
    return payload


def parse_query_range_response(payload: dict) -> tuple[float, float]:
    """Reduces a /api/v1/query_range response to (avg, max) across every sample of every
    returned series. Empty `result` (no series matched, e.g. the KV-hit-rate 0/0 case) -> (0.0,
    0.0), by design -- see module docstring. Raises PrometheusError for a genuine error status."""
    status = payload.get("status")
    if status != "success":
        raise PrometheusError(payload.get("error") or f"Prometheus query failed (status={status!r})")

    data = payload.get("data") or {}
    result = data.get("result") or []
    return reduce_avg_max(result)


def reduce_avg_max(result: list[dict]) -> tuple[float, float]:
    """result: the `data.result` list from a query_range response (each item has `metric` and
    `values: [[epoch, "string-value"], ...]`, matrix format). Never raises; empty/unparseable
    input -> (0.0, 0.0)."""
    values: list[float] = []
    for series in result:
        for point in series.get("values", []):
            if not isinstance(point, (list, tuple)) or len(point) < 2:
                continue
            try:
                v = float(point[1])
            except (TypeError, ValueError):
                continue
            if math.isnan(v) or math.isinf(v):
                continue
            values.append(v)
    if not values:
        return 0.0, 0.0
    return statistics.fmean(values), max(values)


def query_avg_max(base_url: str, expr: str, start: float, end: float, step: int = 5,
                   timeout: float = 10.0) -> tuple[float, float]:
    """Convenience one-shot: query_range() + parse_query_range_response()."""
    payload = query_range(base_url, expr, start, end, step=step, timeout=timeout)
    return parse_query_range_response(payload)

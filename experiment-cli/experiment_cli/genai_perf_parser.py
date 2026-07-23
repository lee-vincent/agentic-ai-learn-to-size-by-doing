"""
experiment_cli/genai_perf_parser.py

Parses `profile_export_genai_perf.csv`, the metrics table written by
`loadgen/genai-perf/run_sweep.sh` per concurrency level (see that directory's README "Expected
output shape"). The real shape (confirmed against an actual genai-perf 0.0.16 run, not guessed)
is three-or-more CSV "blocks" separated by blank lines, all in one file:

  1. A percentile block, header `Metric,avg,min,max,p99,p95,p90,p75,p50,p25,p10,p5,p1`, one row
     per per-request-distribution metric (Time To First Token, Request Latency, Inter Token
     Latency, Output Token Throughput Per User, Output/Input Sequence Length, ...).
  2. A scalar block, header `Metric,Value`, one row per run-level aggregate (Output Token
     Throughput, Request Throughput, Request Count).
  3. (Optional) one or more per-GPU telemetry blocks (`Metric,GPU,avg,...` / `Metric,GPU,Value`)
     -- local NVML/DCGM readings from wherever genai-perf itself ran, NOT the target L40S in
     general. This module intentionally ignores these blocks: GPU/VRAM utilization for the
     results row comes from Prometheus/DCGM Exporter instead (see prometheus_client.py), which is
     genai-perf/README.md's own recommendation ("more properly DCGM Exporter's/Grafana's job").

Numeric cells for large numbers are comma-thousands-separated AND quoted, e.g. `"60,123.33"` --
csv.reader already un-quotes them (it just sees the comma as part of the quoted field, not a
delimiter); this module additionally strips the thousands-separator commas before float().

Known failure mode (documented in run_sweep.sh / genai-perf/README.md): on the chat endpoint,
Qwen3.6's reasoning tokens stream as `reasoning_content`, which genai-perf 0.0.16 cannot count --
every latency metric in the percentile block comes back the literal string `N/A`. This parser
treats `N/A` (and empty cells) as missing (None), never crashes on them, and callers must decide
how to surface a metric that's entirely missing (results.py coerces to 0.0, same as the documented
Prometheus empty-vector case -- see results.py docstring).
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from typing import Optional


class GenaiPerfParseError(Exception):
    """Raised when the CSV doesn't even have the minimum expected block structure."""


# Row (metric) names, exactly as genai-perf 0.0.16 writes them.
_TTFT_ROW = "Time To First Token (ms)"
_ITL_ROW = "Inter Token Latency (ms)"
_REQ_LATENCY_ROW = "Request Latency (ms)"
_TPS_PER_USER_ROW = "Output Token Throughput Per User (tokens/sec/user)"
_OSL_ROW = "Output Sequence Length (tokens)"
_ISL_ROW = "Input Sequence Length (tokens)"

_TPS_AGGREGATE_VALUE = "Output Token Throughput (tokens/sec)"
_REQUEST_THROUGHPUT_VALUE = "Request Throughput (per sec)"
_REQUEST_COUNT_VALUE = "Request Count (count)"


@dataclass
class GenaiPerfMetrics:
    ttft_ms_avg: Optional[float]
    ttft_ms_p50: Optional[float]
    ttft_ms_p90: Optional[float]
    ttft_ms_p99: Optional[float]
    itl_ms_avg: Optional[float]
    itl_ms_p50: Optional[float]
    itl_ms_p90: Optional[float]
    itl_ms_p99: Optional[float]
    request_latency_ms_avg: Optional[float]
    request_latency_ms_p50: Optional[float]
    request_latency_ms_p90: Optional[float]
    request_latency_ms_p99: Optional[float]
    tps_per_user: Optional[float]
    tps_aggregate: Optional[float]
    request_throughput_rps: Optional[float]
    observed_input_len_avg: Optional[float]
    observed_output_len_avg: Optional[float]
    latency_per_output_token_ms: Optional[float]  # == itl_ms_avg, see module docstring / README


def _to_float(raw) -> Optional[float]:
    """'N/A'/''/None -> None. '"60,123.33"' (already unquoted by csv.reader) -> 60123.33."""
    if raw is None:
        return None
    s = str(raw).strip()
    if s == "" or s.upper() in ("N/A", "NA", "NAN"):
        return None
    s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def _split_blocks(rows: list[list[str]]) -> list[list[list[str]]]:
    blocks: list[list[list[str]]] = []
    current: list[list[str]] = []
    for row in rows:
        if not row or all((cell or "").strip() == "" for cell in row):
            if current:
                blocks.append(current)
                current = []
            continue
        current.append(row)
    if current:
        blocks.append(current)
    return blocks


def _parse_percentile_block(block: list[list[str]]) -> tuple[dict, dict]:
    header = [h.strip() for h in block[0]]
    col_index = {name: i for i, name in enumerate(header)}
    rows_by_metric = {row[0].strip(): row for row in block[1:] if row}
    return col_index, rows_by_metric


def _get_pct(col_index: dict, rows_by_metric: dict, metric: str, stat: str) -> Optional[float]:
    row = rows_by_metric.get(metric)
    if row is None:
        return None
    idx = col_index.get(stat)
    if idx is None or idx >= len(row):
        return None
    return _to_float(row[idx])


def parse_csv(path: str) -> GenaiPerfMetrics:
    """Parse one `profile_export_genai_perf.csv`. Raises GenaiPerfParseError if the file is
    missing the minimum expected block structure (percentile block + Metric,Value block)."""
    with open(path, newline="", encoding="utf-8") as f:
        raw_rows = list(csv.reader(f))

    blocks = _split_blocks(raw_rows)
    if not blocks:
        raise GenaiPerfParseError(f"{path}: file is empty or has no parseable CSV rows")

    col_index, pct_rows = _parse_percentile_block(blocks[0])

    value_rows: dict[str, str] = {}
    if len(blocks) > 1:
        value_block = blocks[1]
        header = [h.strip() for h in value_block[0]]
        if header[:2] == ["Metric", "Value"]:
            for row in value_block[1:]:
                if len(row) >= 2:
                    value_rows[row[0].strip()] = row[1]
    else:
        raise GenaiPerfParseError(
            f"{path}: missing the 'Metric,Value' aggregate block (expected a second CSV block "
            "after the percentile block -- see genai-perf/README.md 'Expected output shape')"
        )

    ttft_avg = _get_pct(col_index, pct_rows, _TTFT_ROW, "avg")
    ttft_p50 = _get_pct(col_index, pct_rows, _TTFT_ROW, "p50")
    ttft_p90 = _get_pct(col_index, pct_rows, _TTFT_ROW, "p90")
    ttft_p99 = _get_pct(col_index, pct_rows, _TTFT_ROW, "p99")

    itl_avg = _get_pct(col_index, pct_rows, _ITL_ROW, "avg")
    itl_p50 = _get_pct(col_index, pct_rows, _ITL_ROW, "p50")
    itl_p90 = _get_pct(col_index, pct_rows, _ITL_ROW, "p90")
    itl_p99 = _get_pct(col_index, pct_rows, _ITL_ROW, "p99")

    req_lat_avg = _get_pct(col_index, pct_rows, _REQ_LATENCY_ROW, "avg")
    req_lat_p50 = _get_pct(col_index, pct_rows, _REQ_LATENCY_ROW, "p50")
    req_lat_p90 = _get_pct(col_index, pct_rows, _REQ_LATENCY_ROW, "p90")
    req_lat_p99 = _get_pct(col_index, pct_rows, _REQ_LATENCY_ROW, "p99")

    tps_per_user = _get_pct(col_index, pct_rows, _TPS_PER_USER_ROW, "avg")
    if tps_per_user is None and itl_avg not in (None, 0.0):
        # Fallback per SPEC.md's own equivalence (latency-per-output-token == ITL): per-user
        # output token throughput is the inverse of inter-token latency. Only used if genai-perf's
        # own "Output Token Throughput Per User" row is absent (older genai-perf versions).
        tps_per_user = 1000.0 / itl_avg

    tps_aggregate = _to_float(value_rows.get(_TPS_AGGREGATE_VALUE))
    request_throughput_rps = _to_float(value_rows.get(_REQUEST_THROUGHPUT_VALUE))

    observed_input_len_avg = _get_pct(col_index, pct_rows, _ISL_ROW, "avg")
    observed_output_len_avg = _get_pct(col_index, pct_rows, _OSL_ROW, "avg")

    return GenaiPerfMetrics(
        ttft_ms_avg=ttft_avg, ttft_ms_p50=ttft_p50, ttft_ms_p90=ttft_p90, ttft_ms_p99=ttft_p99,
        itl_ms_avg=itl_avg, itl_ms_p50=itl_p50, itl_ms_p90=itl_p90, itl_ms_p99=itl_p99,
        request_latency_ms_avg=req_lat_avg, request_latency_ms_p50=req_lat_p50,
        request_latency_ms_p90=req_lat_p90, request_latency_ms_p99=req_lat_p99,
        tps_per_user=tps_per_user, tps_aggregate=tps_aggregate,
        request_throughput_rps=request_throughput_rps,
        observed_input_len_avg=observed_input_len_avg,
        observed_output_len_avg=observed_output_len_avg,
        latency_per_output_token_ms=itl_avg,
    )

"""
experiment_cli/harness_parser.py

Parses `summary.json`, written by `loadgen/agent_harness/run_harness.py` at the end of one
invocation (see that file's `_write_results()` and its README's "Expected output shape"). The
`tat_seconds` sub-object is the thing SPEC.md's Turnaround Time metric maps to -- it's measured at
the agent/client layer (not derived from Prometheus), per SPEC.md's explicit instruction.

Real shape (`run_harness.py` `_write_results()`):
    {
      ...,
      "tat_seconds": {"count": N, "avg": float|None, "min": float|None, "max": float|None,
                       "p50": float|None, "p90": float|None}
    }
`p50`/`p90` are absent-as-None when there are 0 or 1 samples (see run_harness.py: `p90` needs
`len(tats) >= 2`). This module treats a missing/None field the same as a missing file: the caller
(results.py) coerces to 0.0, never crashes, never leaves a null in the results row.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional


class HarnessParseError(Exception):
    pass


@dataclass
class HarnessMetrics:
    tat_s_avg: Optional[float]
    tat_s_p50: Optional[float]
    tat_s_p90: Optional[float]
    tat_s_min: Optional[float]
    tat_s_max: Optional[float]
    num_sessions_completed: Optional[int]
    num_sessions_ok: Optional[int]


def _num(value) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_summary(path: str) -> HarnessMetrics:
    with open(path, "r", encoding="utf-8") as f:
        try:
            summary = json.load(f)
        except json.JSONDecodeError as exc:
            raise HarnessParseError(f"{path}: not valid JSON ({exc})") from exc

    tat = summary.get("tat_seconds") or {}
    return HarnessMetrics(
        tat_s_avg=_num(tat.get("avg")),
        tat_s_p50=_num(tat.get("p50")),
        tat_s_p90=_num(tat.get("p90")),
        tat_s_min=_num(tat.get("min")),
        tat_s_max=_num(tat.get("max")),
        num_sessions_completed=summary.get("num_sessions_completed"),
        num_sessions_ok=summary.get("num_sessions_ok"),
    )

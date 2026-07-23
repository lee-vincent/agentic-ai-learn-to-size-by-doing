"""
experiment_cli/report.py

`report` command / helper: prints a human-readable table of sweep result rows. Deliberately
stdlib-only (no tabulate dependency) -- just fixed-width column formatting.
"""
from __future__ import annotations

from experiment_cli import results

# The subset of columns most useful to eyeball after a sweep -- the full schema (results.COLUMNS)
# is wide; `report` shows a curated slice and always points at the CSV for the rest.
_REPORT_COLUMNS = [
    "run_id", "knob_name", "knob_value",
    "ttft_ms_avg", "itl_ms_avg", "tps_aggregate", "request_throughput_rps",
    "tat_s_avg", "cpu_util_pct_avg", "gpu_util_pct_avg", "vram_used_gib_avg",
    "kv_cache_hit_rate_avg",
]


def format_table(rows: list[dict], columns: list[str] | None = None) -> str:
    columns = columns or _REPORT_COLUMNS
    if not rows:
        return "(no rows)"

    def cell(row: dict, col: str) -> str:
        v = row.get(col, "")
        if isinstance(v, float):
            return f"{v:.3f}"
        return str(v)

    widths = {c: max(len(c), *(len(cell(r, c)) for r in rows)) for c in columns}
    lines = []
    header = "  ".join(c.ljust(widths[c]) for c in columns)
    lines.append(header)
    lines.append("  ".join("-" * widths[c] for c in columns))
    for r in rows:
        lines.append("  ".join(cell(r, c).ljust(widths[c]) for c in columns))
    return "\n".join(lines)


def print_table(rows: list[dict], columns: list[str] | None = None) -> None:
    print(format_table(rows, columns=columns))


def cmd_report(args) -> int:
    rows = results.read_rows(args.results_csv)
    print_table(rows)
    print(f"\n{len(rows)} row(s) -- full schema: {args.results_csv}")
    return 0

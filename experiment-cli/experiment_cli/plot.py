"""
experiment_cli/plot.py

`plot` command: given a results.csv (any sweep, any knob), emit one PNG bar chart per metric
comparing every row's `knob_value`, so a human can compare a metric across the swept knob values
after the fact. matplotlib is optional -- if it isn't importable, this prints a clear message and
returns success (exit 0) rather than failing the whole CLI (per the build brief: "graceful
degradation", "do not fail the sweep").
"""
from __future__ import annotations

import os

from experiment_cli import results

# One PNG per metric in this list (skipped individually if the column is absent from the CSV,
# e.g. an older results file from before a schema change) -- covers one representative metric per
# SPEC.md category so a single `plot` invocation gives a useful overview without 50+ files.
DEFAULT_METRICS = [
    "ttft_ms_avg", "ttft_ms_p99",
    "itl_ms_avg", "itl_ms_p99",
    "tps_per_user", "tps_aggregate",
    "latency_per_output_token_ms",
    "request_latency_ms_avg", "request_throughput_rps",
    "tat_s_avg", "tat_s_p90",
    "cpu_util_pct_avg", "ram_used_gib_avg",
    "gpu_util_pct_avg", "vram_used_gib_avg",
    "kv_cache_usage_pct_avg", "kv_cache_hit_rate_avg",
]


def _try_import_matplotlib():
    try:
        import matplotlib
        matplotlib.use("Agg")  # headless -- no display on the EC2 host or in CI
        import matplotlib.pyplot as plt
        return plt
    except ImportError:
        return None


def make_plots(rows: list[dict], output_dir: str, metrics: list[str] | None = None) -> list[str]:
    """Returns the list of PNG paths written. Empty list (with a printed message, no exception) if
    matplotlib isn't importable or there are no rows."""
    plt = _try_import_matplotlib()
    if plt is None:
        print("plot: matplotlib is not imported/installed -- skipping plot generation "
              "(this is not a failure; `pip install matplotlib` to enable plots, then re-run "
              "`python -m experiment_cli plot`).")
        return []
    if not rows:
        print("plot: results file has no rows -- nothing to plot.")
        return []

    metrics = metrics or DEFAULT_METRICS
    os.makedirs(output_dir, exist_ok=True)

    labels = [r.get("knob_value", "") for r in rows]
    knob_name = rows[0].get("knob_name", "knob")
    written = []
    for metric in metrics:
        if metric not in rows[0]:
            continue
        try:
            values = [float(r.get(metric) or 0.0) for r in rows]
        except (TypeError, ValueError):
            continue

        fig, ax = plt.subplots(figsize=(6, 4))
        ax.bar([str(l) for l in labels], values, color="#3b6fa0")
        ax.set_xlabel(knob_name)
        ax.set_ylabel(metric)
        ax.set_title(f"{metric} vs {knob_name}")
        fig.tight_layout()

        out_path = os.path.join(output_dir, f"{metric}.png")
        fig.savefig(out_path)
        plt.close(fig)
        written.append(out_path)

    return written


def cmd_plot(args) -> int:
    rows = results.read_rows(args.results_csv)
    written = make_plots(rows, args.output_dir)
    if written:
        print(f"wrote {len(written)} plot(s) to {args.output_dir}:")
        for p in written:
            print(f"  {p}")
    return 0

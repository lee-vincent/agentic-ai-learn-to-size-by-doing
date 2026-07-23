"""
experiment_cli/cli.py

`python -m experiment_cli {sweep,plot,report}` -- see README.md for the full usage guide. This
module owns argument parsing and the sweep orchestration loop (`run_one()`); the actual work is
delegated to config.py (sweep config), knobs.py (the generic knob abstraction), backends.py (real
vs. --dry-run), genai_perf_parser.py/harness_parser.py/prometheus_client.py (parsing), and
results.py (row assembly + CSV/JSONL append).
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import uuid
from datetime import datetime, timezone

from experiment_cli import config as config_mod
from experiment_cli import genai_perf_parser, harness_parser, knobs, plot, report, results
from experiment_cli.backends import make_backend
from experiment_cli.config import ConfigError
from experiment_cli.genai_perf_parser import GenaiPerfParseError
from experiment_cli.harness_parser import HarnessParseError
from experiment_cli.knobs import KnobError
from experiment_cli.prometheus_client import PROM_EXPRS, PrometheusError, parse_query_range_response
from experiment_cli.runners import RunnerError


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slug(value) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "-" for c in str(value))


def run_one(cfg: dict, knob_spec: knobs.KnobSpec, value, backend, args) -> dict:
    """Runs exactly one sweep point: apply the knob value -> run genai-perf -> run the agent
    harness -> query Prometheus over the exact wall-clock window -> assemble one results row.
    Never restarts vLLM (see knobs.py -- restart-required knobs are refused before this is ever
    called)."""
    base = dict(cfg["fixed"])
    base.update(knob_spec.apply(value, base))

    run_id = f"{cfg['knob']}-{_slug(value)}-{uuid.uuid4().hex[:8]}"
    print(f"=== run {run_id}: {cfg['knob']}={value} ===")

    concurrency = int(base["concurrency"])
    isl_mean = float(base["isl_mean"])
    osl_mean = float(base["osl_mean"])
    reasoning_effort = base.get("reasoning_effort")

    window_start_epoch = time.time()
    window_start_iso = _iso(window_start_epoch)

    genai_run_dir = os.path.join(args.results_dir, "genai-perf", run_id)
    print(f"  -> genai-perf (concurrency={concurrency}, isl_mean={isl_mean}, osl_mean={osl_mean})")
    try:
        gp_result = backend.run_genai_perf(
            script_path=args.genai_perf_script,
            run_dir=genai_run_dir,
            model=cfg["model"],
            served_model_name=cfg["served_model_name"],
            base_url=cfg["genai_perf_base_url"],
            isl_mean=isl_mean, isl_stddev=isl_mean * cfg["isl_stddev_frac"],
            osl_mean=osl_mean, osl_stddev=osl_mean * cfg["osl_stddev_frac"],
            concurrency=concurrency,
            requests_per_concurrency=cfg["requests_per_concurrency"],
            warmup_requests=cfg["warmup_requests"],
            endpoint_type=cfg["endpoint_type"],
            ignore_eos=cfg["ignore_eos"],
            tokenizer=cfg["tokenizer"],
            timeout=args.genai_perf_timeout,
        )
    except RunnerError as exc:
        print(f"FATAL: genai-perf run failed for {run_id}: {exc}", file=sys.stderr)
        raise
    gp_metrics = genai_perf_parser.parse_csv(gp_result.csv_path)

    harness_concurrency = max(1, min(concurrency, cfg["harness_concurrency_cap"]))
    harness_num_sessions = max(2, harness_concurrency * 2)
    harness_out_dir = os.path.join(args.results_dir, "agent_harness", run_id)
    print(f"  -> agent harness (concurrency={harness_concurrency}, "
          f"num_sessions={harness_num_sessions}, reasoning_effort={reasoning_effort})")
    try:
        h_result = backend.run_harness(
            script_path=args.harness_script,
            output_dir=harness_out_dir,
            base_url=cfg["harness_base_url"],
            model=cfg["model"],
            concurrency=harness_concurrency,
            num_sessions=harness_num_sessions,
            isl_mean=isl_mean,
            osl_hint_mean=osl_mean,
            reasoning_effort=reasoning_effort,
            agent_cwd=args.agent_cwd,
            engine=args.engine,
            python_bin=args.python_bin,
            repo_root=args.repo_root,
            timeout=args.harness_timeout,
        )
    except RunnerError as exc:
        print(f"FATAL: agent harness run failed for {run_id}: {exc}", file=sys.stderr)
        raise
    h_metrics = harness_parser.parse_summary(h_result.summary_path)

    window_end_epoch = time.time()
    window_end_iso = _iso(window_end_epoch)

    print(f"  -> Prometheus window [{window_start_iso}, {window_end_iso}]")
    prom_metrics: dict[str, float] = {}
    for key, expr in PROM_EXPRS.items():
        try:
            payload = backend.query_range(
                base_url=cfg["prometheus_url"], expr=expr,
                start=window_start_epoch, end=window_end_epoch, step=cfg["prometheus_step"],
                concurrency=concurrency,
            )
            avg, mx = parse_query_range_response(payload)
        except PrometheusError as exc:
            if args.allow_prometheus_failures:
                print(f"  WARNING: Prometheus query {key!r} failed ({exc}); recording 0.0",
                      file=sys.stderr)
                avg, mx = 0.0, 0.0
            else:
                print(f"FATAL: Prometheus query {key!r} failed: {exc}\n"
                      f"(pass --allow-prometheus-failures to degrade to 0.0 instead of failing "
                      f"the sweep)", file=sys.stderr)
                raise
        prom_metrics[f"{key}_avg"] = avg
        prom_metrics[f"{key}_max"] = mx

    row = results.build_row(
        run_id=run_id,
        window_start_epoch=window_start_epoch, window_start_iso=window_start_iso,
        window_end_epoch=window_end_epoch, window_end_iso=window_end_iso,
        knob_name=cfg["knob"], knob_value=value, model=cfg["model"], base=base,
        gp_metrics=gp_metrics, h_metrics=h_metrics, prom_metrics=prom_metrics,
        gp_csv_path=gp_result.csv_path, gp_manifest_path=gp_result.manifest_path,
        h_summary_path=h_result.summary_path, h_sessions_csv_path=h_result.sessions_csv_path,
    )
    return row


def cmd_sweep(args) -> int:
    cfg = config_mod.load_sweep_config(args.config)
    _apply_cli_overrides(cfg, args)

    knob_spec = knobs.get_knob(cfg["knob"])
    if knob_spec.restart_required:
        print(f"ERROR: knob {cfg['knob']!r} requires a vLLM server restart between values; this "
              f"CLI version does not restart/reconfigure the server (see README.md 'Extension "
              f"points').\n  {knob_spec.description}", file=sys.stderr)
        return 2

    values = cfg["values"]
    if len(values) < 2:
        print(f"WARNING: sweep has only {len(values)} value(s) -- Phase 5 goal expects at least "
              f"two values of one knob.", file=sys.stderr)

    os.makedirs(args.results_dir, exist_ok=True)
    results_csv = os.path.join(args.results_dir, "results.csv")
    results_jsonl = os.path.join(args.results_dir, "results.jsonl")

    backend = make_backend(args.dry_run)
    if args.dry_run:
        print("=== --dry-run: stubbing genai-perf / agent harness / Prometheus with fixtures ===")

    rows = []
    for value in values:
        row = run_one(cfg, knob_spec, value, backend, args)
        results.append_row(results_csv, results_jsonl, row)
        rows.append(row)
        print(f"  wrote row -> {results_csv}\n")

    print(f"=== sweep complete: {len(rows)} row(s) ===")
    report.print_table(rows)
    print(f"\nresults: {results_csv}\nresults (jsonl): {results_jsonl}")
    return 0


def _apply_cli_overrides(cfg: dict, args) -> None:
    """CLI flags win over the config file, which wins over environment-variable defaults baked
    into config.py's with_defaults() -- see README.md 'Configuration precedence'."""
    overrides = {
        "model": args.model,
        "served_model_name": args.served_model_name,
        "genai_perf_base_url": args.genai_perf_base_url,
        "harness_base_url": args.harness_base_url,
        "prometheus_url": args.prometheus_url,
        "requests_per_concurrency": args.requests_per_concurrency,
        "warmup_requests": args.warmup_requests,
        "harness_concurrency_cap": args.harness_concurrency_cap,
    }
    for key, value in overrides.items():
        if value is not None:
            cfg[key] = value


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="experiment_cli",
        description="Phase 5 experiment control CLI: knob sweeps over the live vLLM/genai-perf/"
                     "agent-harness/Prometheus stack (see repo-root SPEC.md/GOALS.md).",
    )
    sub = p.add_subparsers(dest="command", required=True)

    sweep = sub.add_parser("sweep", help="Run one sweep across all values of one knob.")
    sweep.add_argument("--config", required=True,
                        help="Sweep config file (JSON or YAML-lite) -- see sweeps/*.json and "
                             "README.md 'Sweep config format'.")
    sweep.add_argument("--results-dir", default="results",
                        help="Where results.csv/.jsonl and per-run artifact subdirs are written "
                             "(default: ./results).")
    sweep.add_argument("--dry-run", action="store_true",
                        help="Stub genai-perf/agent-harness/Prometheus with fixtures instead of "
                             "invoking the real tools/network -- see README.md 'Dry run'.")
    sweep.add_argument("--genai-perf-script", default=os.environ.get(
        "GENAI_PERF_SCRIPT", "loadgen/genai-perf/run_sweep.sh"),
        help="Path to run_sweep.sh (default: loadgen/genai-perf/run_sweep.sh, or "
             "$GENAI_PERF_SCRIPT).")
    sweep.add_argument("--harness-script", default=os.environ.get(
        "HARNESS_SCRIPT", "loadgen/agent_harness/run_harness.py"),
        help="Path to run_harness.py (default: loadgen/agent_harness/run_harness.py, or "
             "$HARNESS_SCRIPT).")
    sweep.add_argument("--repo-root", default=os.environ.get("EXPERIMENT_CLI_REPO_ROOT", "."),
                        help="cwd for the harness subprocess (needs loadgen/ importable as a "
                             "package from here). Default: '.', or $EXPERIMENT_CLI_REPO_ROOT.")
    sweep.add_argument("--agent-cwd", default=os.environ.get("AGENT_CWD", "."),
                        help="Directory containing the agent/ package, passed through to the "
                             "harness's own --agent-cwd. Default: '.', or $AGENT_CWD.")
    sweep.add_argument("--engine", default=os.environ.get("HARNESS_ENGINE", "cli"),
                        choices=["cli", "import"], help="Passed through to the harness's own "
                        "--engine.")
    sweep.add_argument("--python-bin", default=os.environ.get("HARNESS_PYTHON_BIN"),
                        help="Python interpreter for the harness's --engine cli subprocess "
                             "(default: whatever this CLI itself runs under).")
    sweep.add_argument("--model", default=None,
                        help="Overrides the sweep config's 'model' (default: config file, or "
                             "Qwen/Qwen3.6-27B-FP8).")
    sweep.add_argument("--served-model-name", default=None)
    sweep.add_argument("--genai-perf-base-url", default=None,
                        help="Overrides the sweep config's genai-perf endpoint URL (bare "
                             "host:port, no /v1 -- see loadgen/genai-perf/README.md).")
    sweep.add_argument("--harness-base-url", default=None,
                        help="Overrides the sweep config's agent-harness endpoint URL (includes "
                             "/v1 -- see loadgen/agent_harness/README.md 'gotcha').")
    sweep.add_argument("--prometheus-url", default=None,
                        help="Overrides the sweep config's Prometheus base URL "
                             "(default: http://localhost:9090).")
    sweep.add_argument("--requests-per-concurrency", type=int, default=None)
    sweep.add_argument("--warmup-requests", type=int, default=None)
    sweep.add_argument("--harness-concurrency-cap", type=int, default=None,
                        help="Caps the agent-harness concurrency derived from the concurrency "
                             "knob (harness sessions are much more expensive per-request than "
                             "genai-perf's raw completions). Default: 16, or the config file.")
    sweep.add_argument("--genai-perf-timeout", type=float, default=None,
                        help="Subprocess timeout (seconds) for the genai-perf run.")
    sweep.add_argument("--harness-timeout", type=float, default=None,
                        help="Subprocess timeout (seconds) for the agent-harness run.")
    sweep.add_argument("--allow-prometheus-failures", action="store_true",
                        help="If a Prometheus query fails outright (connection error/non-success "
                             "status -- NOT the documented empty-result-vector case, which always "
                             "records 0.0), record 0.0 and continue instead of failing the sweep.")
    sweep.set_defaults(func=cmd_sweep)

    plot_p = sub.add_parser("plot", help="Emit per-metric PNG comparison charts from a results.csv.")
    plot_p.add_argument("--results-csv", default="results/results.csv")
    plot_p.add_argument("--output-dir", default="results/plots")
    plot_p.set_defaults(func=plot.cmd_plot)

    report_p = sub.add_parser("report", help="Print a human-readable table of a results.csv.")
    report_p.add_argument("--results-csv", default="results/results.csv")
    report_p.set_defaults(func=report.cmd_report)

    return p


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        return args.func(args)
    except (RunnerError, PrometheusError, ConfigError, KnobError, GenaiPerfParseError,
            HarnessParseError) as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

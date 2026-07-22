#!/usr/bin/env python3
"""
loadgen/agent_harness/run_harness.py

Agent-driven load harness (Phase 3 / SPEC.md): runs N concurrent sessions of the Phase 3 agent
against the vLLM endpoint to produce agentic-shaped traffic (multi-turn tool-call loops, variable
output length driven by the model's own behavior, burstier concurrency) that genai-perf alone --
being a raw-completions benchmarker -- cannot reproduce. Logs turnaround time (TAT) per session.

`agent/` is built in a parallel worktree and is not present here. See README.md "Assumed agent/
interface" for the exact contract this file's adapter (adapter.py) codes against, and
`loadgen/agent_harness/_selftest/` for how this harness's concurrency/logging logic was validated
without a real agent/ or a real vLLM endpoint (see README.md "Verified vs. deferred").

Usage (once agent/ exists and a real vLLM endpoint is reachable):
    python -m loadgen.agent_harness.run_harness \\
      --base-url http://<vllm-host>:8000/v1 --model Qwen/Qwen3.6-27B-FP8 \\
      --concurrency 8 --num-sessions 40 --isl-mean 60 --osl-hint-mean 200 \\
      --agent-cwd /path/to/repo/agent-worktree

Run `python -m loadgen.agent_harness.run_harness --help` for the full flag list.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
import statistics
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from adapter import AgentInterfaceError, NormalizedResult, make_engine  # noqa: E402
from synthetic_tasks import SyntheticTaskConfig, generate_tasks  # noqa: E402


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run_harness.py",
        description="Drive N concurrent agent sessions against a vLLM endpoint and log TAT/session.",
    )
    # --- endpoint / model (SPEC.md: not hardcoded to one deployment) ---
    p.add_argument("--base-url", default="http://localhost:8000/v1",
                    help="OpenAI-compatible base URL, INCLUDING the /v1 suffix (the agent's own "
                         "default convention -- see README.md 'gotcha: base-url conventions "
                         "differ from genai-perf').")
    p.add_argument("--model", default="Qwen/Qwen3.6-27B-FP8")
    p.add_argument("--reasoning-effort", default=None, choices=["off", "low", "medium", "high"],
                    help="Passthrough to the agent; omit to use the agent's own default.")
    p.add_argument("--max-tokens", type=int, default=None, help="Passthrough to the agent.")

    # --- concurrency / volume knobs (SPEC.md) ---
    p.add_argument("--concurrency", type=int, default=4,
                    help="Number of concurrent agent sessions in flight at once (closed-loop: "
                         "as one session finishes, the next queued task starts immediately).")
    p.add_argument("--num-sessions", type=int, default=None,
                    help="Total sessions to run across the whole invocation. Default: concurrency * 5.")
    p.add_argument("--arrival-pattern", choices=["closed", "poisson"], default="closed",
                    help="'closed' (default): exactly --concurrency sessions in flight at all "
                         "times until --num-sessions complete -- matches genai-perf's own "
                         "concurrency semantics. 'poisson': open-loop bursty arrivals at "
                         "--arrival-rate sessions/sec, capped at --concurrency in flight -- closer "
                         "to real bursty user traffic; some arrivals may queue if the cap is hit.")
    p.add_argument("--arrival-rate", type=float, default=1.0,
                    help="Sessions/sec for --arrival-pattern poisson (mean of the exponential "
                         "inter-arrival distribution).")

    # --- input/output length knobs (SPEC.md) ---
    p.add_argument("--isl-mean", type=float, default=40.0, help="Avg input length, in words.")
    p.add_argument("--isl-stddev", type=float, default=10.0)
    p.add_argument("--osl-hint-mean", type=float, default=150.0,
                    help="Best-effort target output length hint baked into the task text, in "
                         "words -- see synthetic_tasks.py docstring for why this is a hint, not "
                         "an enforced value.")
    p.add_argument("--osl-hint-stddev", type=float, default=50.0)
    p.add_argument("--tool-mix", type=float, default=0.7,
                    help="Fraction of synthetic tasks that include a tool-triggering question.")
    p.add_argument("--tasks-file", default=None,
                    help="Optional: newline-delimited real tasks, sampled with replacement to "
                         "fill --num-sessions, instead of synthetic generation.")

    # --- adapter / engine selection (see adapter.py / README.md) ---
    p.add_argument("--engine", choices=["cli", "import"], default="cli")
    p.add_argument("--agent-cwd", default=".",
                    help="Directory containing the agent/ package (cwd for --engine cli's "
                         "subprocess, or the sys.path entry for --engine import).")
    p.add_argument("--python-bin", default=None, help="Python interpreter for --engine cli.")
    p.add_argument("--session-timeout-seconds", type=float, default=180.0)

    # --- output ---
    p.add_argument("--output-dir", default=None,
                    help="Default: loadgen/results/agent_harness/<UTC timestamp>/")
    p.add_argument("--seed", type=int, default=None)
    return p


def _default_output_dir() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return os.path.join(here, "..", "results", "agent_harness", ts)


def _load_tasks_from_file(path: str, n: int, rng: random.Random) -> list[str]:
    with open(path, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]
    if not lines:
        raise SystemExit(f"--tasks-file {path!r} contained no non-empty lines")
    return [rng.choice(lines) for _ in range(n)]


def _run_closed_loop(engine, tasks, args, results: list, errors: list) -> None:
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = {
            pool.submit(
                engine.run_session,
                task=task, base_url=args.base_url, model=args.model,
                session_id=str(uuid.uuid4()), reasoning_effort=args.reasoning_effort,
                max_tokens=args.max_tokens, timeout_seconds=args.session_timeout_seconds,
            ): task
            for task in tasks
        }
        for fut in as_completed(futures):
            try:
                results.append(fut.result())
            except AgentInterfaceError as exc:
                errors.append(str(exc))


def _run_poisson(engine, tasks, args, results: list, errors: list) -> None:
    rng = random.Random(args.seed)
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = []
        for task in tasks:
            futures.append(pool.submit(
                engine.run_session,
                task=task, base_url=args.base_url, model=args.model,
                session_id=str(uuid.uuid4()), reasoning_effort=args.reasoning_effort,
                max_tokens=args.max_tokens, timeout_seconds=args.session_timeout_seconds,
            ))
            if task is not tasks[-1]:
                time.sleep(rng.expovariate(args.arrival_rate))
        for fut in as_completed(futures):
            try:
                results.append(fut.result())
            except AgentInterfaceError as exc:
                errors.append(str(exc))


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)
    num_sessions = args.num_sessions or args.concurrency * 5
    output_dir = args.output_dir or _default_output_dir()
    os.makedirs(output_dir, exist_ok=True)

    rng = random.Random(args.seed)
    if args.tasks_file:
        tasks = _load_tasks_from_file(args.tasks_file, num_sessions, rng)
    else:
        task_cfg = SyntheticTaskConfig(
            isl_mean=args.isl_mean, isl_stddev=args.isl_stddev,
            osl_hint_mean=args.osl_hint_mean, osl_hint_stddev=args.osl_hint_stddev,
            tool_mix=args.tool_mix, seed=args.seed,
        )
        tasks = generate_tasks(num_sessions, task_cfg)

    engine = make_engine(args.engine, agent_cwd=args.agent_cwd, python_bin=args.python_bin)

    print(f"=== agent load harness ===")
    print(f"base_url={args.base_url} model={args.model} engine={args.engine}")
    print(f"concurrency={args.concurrency} num_sessions={num_sessions} "
          f"arrival_pattern={args.arrival_pattern}")
    print(f"isl_mean={args.isl_mean} osl_hint_mean={args.osl_hint_mean}")
    print(f"output_dir={output_dir}")
    print()

    results: list[NormalizedResult] = []
    errors: list[str] = []
    wall_start = time.monotonic()
    try:
        if args.arrival_pattern == "closed":
            _run_closed_loop(engine, tasks, args, results, errors)
        else:
            _run_poisson(engine, tasks, args, results, errors)
    except AgentInterfaceError as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return 2
    wall_elapsed = time.monotonic() - wall_start

    if errors:
        print(f"WARNING: {len(errors)} session(s) raised AgentInterfaceError "
              f"(agent/ contract mismatch, not a normal per-session failure):", file=sys.stderr)
        for e in errors[:5]:
            print(f"  - {e}", file=sys.stderr)

    _write_results(output_dir, args, num_sessions, wall_elapsed, results)
    return 0 if not errors else 2


def _write_results(output_dir, args, num_sessions, wall_elapsed, results: list[NormalizedResult]) -> None:
    jsonl_path = os.path.join(output_dir, "sessions.jsonl")
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(asdict(r)) + "\n")

    csv_path = os.path.join(output_dir, "sessions.csv")
    fieldnames = [
        "session_id", "status", "error", "tat_seconds", "harness_observed_seconds",
        "num_model_calls", "num_tool_calls", "prompt_tokens", "completion_tokens",
        "log_path", "engine",
    ]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in results:
            w.writerow({k: getattr(r, k) for k in fieldnames})

    tats = [r.tat_seconds for r in results if r.tat_seconds is not None]
    ok_count = sum(1 for r in results if r.status == "ok")
    summary = {
        "base_url": args.base_url,
        "model": args.model,
        "engine": args.engine,
        "concurrency": args.concurrency,
        "num_sessions_requested": num_sessions,
        "num_sessions_completed": len(results),
        "num_sessions_ok": ok_count,
        "isl_mean": args.isl_mean,
        "osl_hint_mean": args.osl_hint_mean,
        "arrival_pattern": args.arrival_pattern,
        "wall_clock_seconds": wall_elapsed,
        "sessions_per_sec": (len(results) / wall_elapsed) if wall_elapsed > 0 else None,
        "tat_seconds": {
            "count": len(tats),
            "avg": statistics.fmean(tats) if tats else None,
            "min": min(tats) if tats else None,
            "max": max(tats) if tats else None,
            "p50": statistics.median(tats) if tats else None,
            "p90": (statistics.quantiles(tats, n=10)[8] if len(tats) >= 2 else (tats[0] if tats else None)),
        },
    }
    summary_path = os.path.join(output_dir, "summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    print(f"--- summary ---")
    print(json.dumps(summary, indent=2))
    print()
    print(f"per-session rows: {csv_path}")
    print(f"per-session jsonl: {jsonl_path}")
    print(f"summary: {summary_path}")


if __name__ == "__main__":
    sys.exit(main())

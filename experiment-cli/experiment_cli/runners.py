"""
experiment_cli/runners.py

The REAL backend: invokes the two already-live load tools exactly the way they're designed to be
invoked (see their own READMEs, quoted in each function's docstring) --
  - loadgen/genai-perf/run_sweep.sh   as a `bash` subprocess, config via environment variables.
  - loadgen/agent_harness/run_harness.py as a `python <script>` subprocess (NOT `python -m ...` --
    matches loadgen/scripts/run_local_validation.sh's own invocation convention exactly), config
    via CLI flags.

No path here is hardcoded to /opt/... or any other absolute deploy-time location -- every path is
a parameter, defaulted from a CLI flag/env var one layer up in cli.py. This module intentionally
does NOT try to reach docker or spawn genai-perf/vLLM containers itself (no docker-in-docker) --
per the build brief, on the real instance this whole CLI runs inside the same container/image
that already has `genai-perf` on PATH and a checkout of `loadgen/`, so a plain subprocess is
correct here.
"""
from __future__ import annotations

import glob
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Optional


class RunnerError(Exception):
    pass


@dataclass
class GenaiPerfRunResult:
    csv_path: str
    manifest_path: str
    run_dir: str
    returncode: int
    stdout: str
    stderr: str


@dataclass
class HarnessRunResult:
    summary_path: str
    sessions_csv_path: str
    output_dir: str
    returncode: int
    stdout: str
    stderr: str


def _find_genai_perf_csv(run_dir: str, served_model_name: str, endpoint_type: str,
                          concurrency: int) -> str:
    """run_sweep.sh's own documented path convention (see its line printing this exact pattern):
    $ARTIFACT_DIR/concurrency<N>/<served_model_name_with_/_as__>-openai-<endpoint_type>-concurrency<N>/
    profile_export_genai_perf.csv
    Falls back to a recursive glob under the concurrency<N> subdirectory if genai-perf's own
    internal naming ever drifts from that convention (e.g. a version bump) -- so this doesn't
    silently break on a cosmetic rename."""
    safe_name = served_model_name.replace("/", "_")
    concurrency_dir = os.path.join(run_dir, f"concurrency{concurrency}")
    expected = os.path.join(
        concurrency_dir, f"{safe_name}-openai-{endpoint_type}-concurrency{concurrency}",
        "profile_export_genai_perf.csv",
    )
    if os.path.isfile(expected):
        return expected

    matches = glob.glob(os.path.join(concurrency_dir, "**", "profile_export_genai_perf.csv"),
                         recursive=True)
    if matches:
        return sorted(matches)[0]

    raise RunnerError(
        f"could not find profile_export_genai_perf.csv under {concurrency_dir!r} "
        f"(expected {expected!r}) -- see loadgen/genai-perf/README.md 'Expected output shape'"
    )


def run_genai_perf(
    *,
    script_path: str,
    run_dir: str,
    model: str,
    served_model_name: str,
    base_url: str,
    isl_mean: float,
    isl_stddev: float,
    osl_mean: float,
    osl_stddev: float,
    concurrency: int,
    requests_per_concurrency: int,
    warmup_requests: int,
    endpoint_type: str = "completions",
    ignore_eos: bool = True,
    tokenizer: Optional[str] = None,
    extra_env: Optional[dict] = None,
    timeout: Optional[float] = None,
) -> GenaiPerfRunResult:
    """Invokes `bash <script_path>` (loadgen/genai-perf/run_sweep.sh) with CONCURRENCY_LIST set
    to this ONE concurrency value (one run == one knob value == one genai-perf profile), per that
    script's own env-var contract (see its header comment / README.md 'Knobs' table)."""
    env = os.environ.copy()
    env.update({
        "MODEL_ID": model,
        "SERVED_MODEL_NAME": served_model_name,
        "BASE_URL": base_url,
        # genai-perf's --synthetic-input-tokens-mean/--output-tokens-mean take ints only; a
        # "200.0" from float-typed config is rejected with "invalid int value", so round here.
        "ISL_MEAN": str(int(round(isl_mean))),
        "ISL_STDDEV": str(int(round(isl_stddev))),
        "OSL_MEAN": str(int(round(osl_mean))),
        "OSL_STDDEV": str(int(round(osl_stddev))),
        "CONCURRENCY_LIST": str(concurrency),
        "REQUESTS_PER_CONCURRENCY": str(requests_per_concurrency),
        "WARMUP_REQUESTS": str(warmup_requests),
        "ENDPOINT_TYPE": endpoint_type,
        "IGNORE_EOS": "true" if ignore_eos else "false",
        "ARTIFACT_DIR": run_dir,
    })
    if tokenizer:
        env["TOKENIZER"] = tokenizer
    if extra_env:
        env.update({k: str(v) for k, v in extra_env.items()})

    proc = subprocess.run(
        ["bash", script_path], env=env, capture_output=True, text=True, timeout=timeout,
    )
    if proc.returncode != 0:
        raise RunnerError(
            f"{script_path} exited {proc.returncode}\n--- stdout ---\n{proc.stdout}\n"
            f"--- stderr ---\n{proc.stderr}"
        )

    csv_path = _find_genai_perf_csv(run_dir, served_model_name, endpoint_type, concurrency)
    manifest_path = os.path.join(run_dir, "manifest.json")
    return GenaiPerfRunResult(
        csv_path=csv_path, manifest_path=manifest_path, run_dir=run_dir,
        returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr,
    )


def run_harness(
    *,
    script_path: str,
    output_dir: str,
    base_url: str,
    model: str,
    concurrency: int,
    num_sessions: int,
    isl_mean: float,
    osl_hint_mean: float,
    reasoning_effort: Optional[str],
    agent_cwd: str,
    engine: str = "cli",
    python_bin: Optional[str] = None,
    repo_root: str = ".",
    timeout: Optional[float] = None,
) -> HarnessRunResult:
    """Invokes `python <script_path>` (loadgen/agent_harness/run_harness.py) exactly the way
    loadgen/scripts/run_local_validation.sh does it -- a direct script path, not `python -m`.
    Exit code 0 = every session ok; 2 = the harness ran to completion but >=1 session had a
    non-'ok' terminal status (still a valid, parseable summary.json -- not a runner failure, see
    run_harness.py's own `main()`). Any other exit code is a real crash."""
    cmd = [
        python_bin or sys.executable, script_path,
        "--base-url", base_url,
        "--model", model,
        "--concurrency", str(concurrency),
        "--num-sessions", str(num_sessions),
        "--isl-mean", str(isl_mean),
        "--osl-hint-mean", str(osl_hint_mean),
        "--engine", engine,
        "--agent-cwd", agent_cwd,
        "--output-dir", output_dir,
    ]
    if reasoning_effort:
        # "off" is itself a valid, explicit choice in run_harness.py's argparse (choices=
        # off/low/medium/high) -- pass it through so the harness actually requests it rather than
        # falling back to the agent's own undocumented default, keeping this row's
        # reasoning_effort_configured column truthful about what was actually requested.
        cmd += ["--reasoning-effort", reasoning_effort]

    proc = subprocess.run(
        cmd, cwd=repo_root, capture_output=True, text=True, timeout=timeout,
    )
    if proc.returncode not in (0, 2):
        raise RunnerError(
            f"{script_path} exited {proc.returncode}\n--- stdout ---\n{proc.stdout}\n"
            f"--- stderr ---\n{proc.stderr}"
        )

    summary_path = os.path.join(output_dir, "summary.json")
    sessions_csv_path = os.path.join(output_dir, "sessions.csv")
    if not os.path.isfile(summary_path):
        raise RunnerError(f"{script_path} did not write {summary_path!r} (exit {proc.returncode})")

    return HarnessRunResult(
        summary_path=summary_path, sessions_csv_path=sessions_csv_path, output_dir=output_dir,
        returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr,
    )

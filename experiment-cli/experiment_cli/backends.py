"""
experiment_cli/backends.py

Two backends, same interface, selected once in cli.py by `--dry-run`:
  - RealBackend: subprocess.run() the real tools (runners.py) and urllib the real Prometheus
    (prometheus_client.py).
  - DryRunBackend: writes the same-shaped fixture files (dry_run.py) instead of running anything,
    and returns raw Prometheus-shaped payloads instead of doing an HTTP call.

cli.py's run_one() calls the SAME downstream parsers (genai_perf_parser.parse_csv,
harness_parser.parse_summary, prometheus_client.parse_query_range_response) regardless of which
backend produced the inputs -- only the I/O layer differs. This is deliberate: it's what makes
`--dry-run` a real mechanical test of the parsing/assembly pipeline, not a separate untested path.
"""
from __future__ import annotations

import os
from typing import Protocol

from experiment_cli import dry_run, prometheus_client, runners


class Backend(Protocol):
    def run_genai_perf(self, **kwargs) -> "runners.GenaiPerfRunResult": ...
    def run_harness(self, **kwargs) -> "runners.HarnessRunResult": ...
    def query_range(self, *, base_url: str, expr: str, start: float, end: float, step: int) -> dict: ...


class RealBackend:
    def run_genai_perf(self, **kwargs) -> runners.GenaiPerfRunResult:
        return runners.run_genai_perf(**kwargs)

    def run_harness(self, **kwargs) -> runners.HarnessRunResult:
        return runners.run_harness(**kwargs)

    def query_range(self, *, base_url: str, expr: str, start: float, end: float, step: int,
                     **_ignored) -> dict:
        return prometheus_client.query_range(base_url, expr, start, end, step=step)


class DryRunBackend:
    """Stubs the three externals per --dry-run (see dry_run.py). Accepts (and ignores) every
    keyword the real backend needs so cli.py's run_one() can call both backends identically."""

    def run_genai_perf(self, *, run_dir: str, concurrency: int, isl_mean: float, osl_mean: float,
                        served_model_name: str, endpoint_type: str = "completions",
                        **_ignored) -> runners.GenaiPerfRunResult:
        csv_path = dry_run.write_fixture_genai_perf_csv(
            run_dir, concurrency, isl_mean, osl_mean, served_model_name, endpoint_type,
        )
        manifest_path = os.path.join(run_dir, "manifest.json")
        return runners.GenaiPerfRunResult(
            csv_path=csv_path, manifest_path=manifest_path, run_dir=run_dir,
            returncode=0, stdout="(--dry-run: fixture written, run_sweep.sh not invoked)", stderr="",
        )

    def run_harness(self, *, output_dir: str, concurrency: int, num_sessions: int, base_url: str,
                     model: str, **_ignored) -> runners.HarnessRunResult:
        summary_path = dry_run.write_fixture_harness_summary(
            output_dir, concurrency, num_sessions, base_url, model,
        )
        sessions_csv_path = os.path.join(output_dir, "sessions.csv")
        return runners.HarnessRunResult(
            summary_path=summary_path, sessions_csv_path=sessions_csv_path, output_dir=output_dir,
            returncode=0, stdout="(--dry-run: fixture written, run_harness.py not invoked)", stderr="",
        )

    def query_range(self, *, base_url: str, expr: str, start: float, end: float, step: int,
                     concurrency: int = 1, **_ignored) -> dict:
        return dry_run.fixture_prometheus_payload(expr, concurrency)


def make_backend(dry_run_mode: bool) -> Backend:
    return DryRunBackend() if dry_run_mode else RealBackend()

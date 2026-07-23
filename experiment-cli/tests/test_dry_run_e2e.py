"""
tests/test_dry_run_e2e.py

The one OFFLINE dry-run end-to-end check required by the build brief: `experiment_cli sweep
--dry-run` against the checked-in concurrency_1_8.json sweep config, with NO network access and
NO subprocess spawn of genai-perf/the agent harness/Prometheus -- backends.DryRunBackend (see
dry_run.py) writes fixture files in their place, but the SAME parsing/row-assembly code
(genai_perf_parser, harness_parser, prometheus_client, results) that a real sweep uses. Confirms
results.csv ends up with exactly 2 rows (concurrency 1 and 8) and every column populated.
"""
import csv
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from experiment_cli.cli import main
from experiment_cli import results

EXPERIMENT_CLI_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SWEEP_CONFIG = os.path.join(EXPERIMENT_CLI_DIR, "sweeps", "concurrency_1_8.json")


class TestDryRunEndToEnd(unittest.TestCase):
    def test_concurrency_sweep_dry_run_produces_two_populated_rows(self):
        with tempfile.TemporaryDirectory() as tmp_results:
            rc = main([
                "sweep",
                "--config", SWEEP_CONFIG,
                "--results-dir", tmp_results,
                "--dry-run",
            ])
            self.assertEqual(rc, 0)

            results_csv = os.path.join(tmp_results, "results.csv")
            results_jsonl = os.path.join(tmp_results, "results.jsonl")
            self.assertTrue(os.path.isfile(results_csv))
            self.assertTrue(os.path.isfile(results_jsonl))

            with open(results_csv, newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))

            self.assertEqual(len(rows), 2, "expected exactly one row per swept concurrency value")
            self.assertEqual(rows[0]["knob_value"], "1")
            self.assertEqual(rows[1]["knob_value"], "8")
            self.assertEqual(rows[0]["knob_name"], "concurrency")

            for row in rows:
                for col in results.COLUMNS:
                    self.assertIn(col, row, f"missing column {col!r}")
                    self.assertNotEqual(row[col], "", f"column {col!r} is empty in row {row['run_id']!r}")

            # Load carries a real signal that concurrency actually changed something (dry-run
            # fixtures are deliberately monotonic in concurrency -- see dry_run.py docstring).
            ttft_c1 = float(rows[0]["ttft_ms_avg"])
            ttft_c8 = float(rows[1]["ttft_ms_avg"])
            self.assertGreater(ttft_c8, ttft_c1)

            # KV cache hit rate is deliberately an empty Prometheus vector in the dry-run fixture
            # (see dry_run.fixture_prometheus_payload) -- must show up as 0.0, not blank/null.
            self.assertEqual(rows[0]["kv_cache_hit_rate_avg"], "0.0")
            self.assertEqual(rows[0]["kv_cache_hit_rate_max"], "0.0")

            # Artifact paths exist for real (dry-run still writes real fixture files to disk).
            self.assertTrue(os.path.isfile(rows[0]["genai_perf_csv_path"]))
            self.assertTrue(os.path.isfile(rows[0]["harness_summary_path"]))

    def test_precision_knob_is_refused_not_silently_run(self):
        # precision requires a vLLM restart -- this CLI version must refuse, not silently produce
        # a wrong/empty row (see knobs.py / README.md "Extension points").
        with tempfile.TemporaryDirectory() as tmp:
            config_path = os.path.join(tmp, "bad_sweep.json")
            with open(config_path, "w", encoding="utf-8") as f:
                f.write('{"knob": "precision", "values": ["fp8", "int4"]}')
            rc = main(["sweep", "--config", config_path, "--results-dir", tmp, "--dry-run"])
            self.assertNotEqual(rc, 0)
            self.assertFalse(os.path.isfile(os.path.join(tmp, "results.csv")))


if __name__ == "__main__":
    unittest.main()

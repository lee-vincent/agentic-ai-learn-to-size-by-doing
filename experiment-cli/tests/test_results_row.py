"""tests/test_results_row.py -- results-row assembly (all COLUMNS present) and CSV/JSONL append."""
import csv
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from experiment_cli import genai_perf_parser, harness_parser, results
from experiment_cli.genai_perf_parser import GenaiPerfMetrics
from experiment_cli.harness_parser import HarnessMetrics

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _make_row(prom_metrics=None):
    gp_metrics = genai_perf_parser.parse_csv(os.path.join(FIXTURES, "genai_perf_concurrency1.csv"))
    h_metrics = harness_parser.parse_summary(os.path.join(FIXTURES, "harness_summary.json"))
    base = {
        "precision": "fp8",
        "kv_cache_strategy": "paged_attention+prefix_caching",
        "decoding_algorithm": "greedy",
        "concurrency": 1,
        "isl_mean": 200,
        "osl_mean": 200,
        "reasoning_effort": "off",
    }
    if prom_metrics is None:
        prom_metrics = {
            "cpu_util_pct_avg": 20.0, "cpu_util_pct_max": 35.0,
            "ram_used_gib_avg": 12.0, "ram_used_gib_max": 14.0,
            "gpu_util_pct_avg": 22.0, "gpu_util_pct_max": 40.0,
            "vram_used_gib_avg": 31.0, "vram_used_gib_max": 32.0,
            "kv_cache_usage_pct_avg": 5.0, "kv_cache_usage_pct_max": 9.0,
            "kv_cache_hit_rate_avg": 0.0, "kv_cache_hit_rate_max": 0.0,
        }
    return results.build_row(
        run_id="concurrency-1-abcd1234",
        window_start_epoch=1700000000.0, window_start_iso="2023-11-14T22:13:20Z",
        window_end_epoch=1700000030.0, window_end_iso="2023-11-14T22:13:50Z",
        knob_name="concurrency", knob_value=1, model="Qwen/Qwen3.6-27B-FP8", base=base,
        gp_metrics=gp_metrics, h_metrics=h_metrics, prom_metrics=prom_metrics,
        gp_csv_path="results/genai-perf/run1/.../profile_export_genai_perf.csv",
        gp_manifest_path="results/genai-perf/run1/manifest.json",
        h_summary_path="results/agent_harness/run1/summary.json",
        h_sessions_csv_path="results/agent_harness/run1/sessions.csv",
    )


class TestResultsRow(unittest.TestCase):
    def test_all_columns_present(self):
        row = _make_row()
        for col in results.COLUMNS:
            self.assertIn(col, row, f"missing column {col!r}")

    def test_no_column_is_none(self):
        row = _make_row()
        for col in results.COLUMNS:
            self.assertIsNotNone(row[col], f"column {col!r} is None (should be coerced to 0.0)")

    def test_kv_cache_hit_rate_empty_vector_is_zero(self):
        # The documented caveat, exercised at the results-row level: a run where Prometheus
        # returned an empty vector for the KV-hit-rate query records 0.0, not null/None/NaN.
        row = _make_row(prom_metrics={
            "cpu_util_pct_avg": 0.0, "cpu_util_pct_max": 0.0,
            "ram_used_gib_avg": 0.0, "ram_used_gib_max": 0.0,
            "gpu_util_pct_avg": 0.0, "gpu_util_pct_max": 0.0,
            "vram_used_gib_avg": 0.0, "vram_used_gib_max": 0.0,
            "kv_cache_usage_pct_avg": 0.0, "kv_cache_usage_pct_max": 0.0,
            # kv_cache_hit_rate_avg/_max deliberately absent, as prometheus_client.py's
            # reduce_avg_max() would produce for an empty result list handed straight through.
        })
        self.assertEqual(row["kv_cache_hit_rate_avg"], 0.0)
        self.assertEqual(row["kv_cache_hit_rate_max"], 0.0)

    def test_knob_tagging_reflects_full_configuration(self):
        row = _make_row()
        self.assertEqual(row["knob_name"], "concurrency")
        self.assertEqual(row["knob_value"], 1)
        self.assertEqual(row["precision"], "fp8")
        self.assertEqual(row["kv_cache_strategy"], "paged_attention+prefix_caching")
        self.assertEqual(row["decoding_algorithm"], "greedy")
        self.assertEqual(row["concurrency"], 1.0)
        self.assertEqual(row["isl_mean_configured"], 200.0)
        self.assertEqual(row["osl_mean_configured"], 200.0)
        self.assertEqual(row["reasoning_effort_configured"], "off")
        self.assertEqual(row["reasoning_effort_observed"], "off")
        # Observed (SPEC.md) input/output length come from genai-perf, distinct from configured.
        self.assertEqual(row["observed_input_len_avg"], 200.0)
        self.assertEqual(row["observed_output_len_avg"], 51.0)

    def test_latency_per_output_token_equals_itl(self):
        row = _make_row()
        self.assertEqual(row["latency_per_output_token_ms"], row["itl_ms_avg"])

    def test_append_row_writes_csv_and_jsonl_with_header(self):
        row1 = _make_row()
        row2 = _make_row()
        row2["run_id"] = "concurrency-8-efgh5678"
        row2["knob_value"] = 8
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = os.path.join(tmp, "results.csv")
            jsonl_path = os.path.join(tmp, "results.jsonl")
            results.append_row(csv_path, jsonl_path, row1)
            results.append_row(csv_path, jsonl_path, row2)

            with open(csv_path, newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["knob_value"], "1")
            self.assertEqual(rows[1]["knob_value"], "8")
            for col in results.COLUMNS:
                self.assertIn(col, rows[0])

            with open(jsonl_path, encoding="utf-8") as f:
                jsonl_rows = [json.loads(line) for line in f]
            self.assertEqual(len(jsonl_rows), 2)
            self.assertEqual(jsonl_rows[1]["run_id"], "concurrency-8-efgh5678")


if __name__ == "__main__":
    unittest.main()

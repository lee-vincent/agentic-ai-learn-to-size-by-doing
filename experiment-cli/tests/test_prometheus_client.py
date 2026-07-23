"""tests/test_prometheus_client.py -- Prometheus query_range response parsing, including the
REQUIRED empty-vector -> 0.0 behavior (see prometheus_client.py module docstring)."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from experiment_cli.prometheus_client import PrometheusError, parse_query_range_response

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _load(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return json.load(f)


class TestPrometheusClient(unittest.TestCase):
    def test_success_matrix_reduces_to_avg_max(self):
        payload = _load("prometheus_matrix_success.json")
        avg, mx = parse_query_range_response(payload)
        # values: 20.0, 25.0, 30.0, 22.5 -> avg 24.375, max 30.0
        self.assertAlmostEqual(avg, 24.375)
        self.assertAlmostEqual(mx, 30.0)

    def test_empty_result_vector_is_zero_not_none_not_a_crash(self):
        # THE documented KV-cache-hit-rate caveat (this model observed to keep
        # vllm:prefix_cache_hits_total at 0, so the ratio query returns no series at all -- 0/0 is
        # undefined in PromQL, not zero). Must be exactly 0.0, never None, never raise.
        payload = _load("prometheus_empty.json")
        avg, mx = parse_query_range_response(payload)
        self.assertEqual(avg, 0.0)
        self.assertEqual(mx, 0.0)
        self.assertIsInstance(avg, float)
        self.assertIsInstance(mx, float)

    def test_error_status_raises_prometheus_error(self):
        payload = _load("prometheus_error.json")
        with self.assertRaises(PrometheusError):
            parse_query_range_response(payload)

    def test_nan_and_malformed_points_are_skipped_not_crashed(self):
        payload = {
            "status": "success",
            "data": {"resultType": "matrix", "result": [
                {"metric": {}, "values": [[1, "nan"], [2, "not-a-number"], [3, "5.0"], [4]]},
            ]},
        }
        avg, mx = parse_query_range_response(payload)
        self.assertEqual(avg, 5.0)
        self.assertEqual(mx, 5.0)


if __name__ == "__main__":
    unittest.main()

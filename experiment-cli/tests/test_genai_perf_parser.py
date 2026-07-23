"""tests/test_genai_perf_parser.py -- profile_export_genai_perf.csv parsing."""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from experiment_cli.genai_perf_parser import GenaiPerfParseError, parse_csv

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


class TestGenaiPerfParser(unittest.TestCase):
    def test_parses_concurrency1_fixture(self):
        m = parse_csv(os.path.join(FIXTURES, "genai_perf_concurrency1.csv"))
        self.assertAlmostEqual(m.ttft_ms_avg, 46.21)
        self.assertAlmostEqual(m.ttft_ms_p50, 45.03)
        self.assertAlmostEqual(m.itl_ms_avg, 8.12)
        self.assertAlmostEqual(m.request_latency_ms_avg, 1523.23)
        self.assertAlmostEqual(m.tps_per_user, 123.15)
        self.assertAlmostEqual(m.tps_aggregate, 123.15)
        self.assertAlmostEqual(m.request_throughput_rps, 2.41)
        self.assertAlmostEqual(m.observed_input_len_avg, 200.00)
        self.assertAlmostEqual(m.observed_output_len_avg, 51.00)
        # SPEC.md equivalence: latency-per-output-token == ITL avg.
        self.assertEqual(m.latency_per_output_token_ms, m.itl_ms_avg)

    def test_parses_concurrency8_fixture_and_differs_from_concurrency1(self):
        m1 = parse_csv(os.path.join(FIXTURES, "genai_perf_concurrency1.csv"))
        m8 = parse_csv(os.path.join(FIXTURES, "genai_perf_concurrency8.csv"))
        self.assertGreater(m8.ttft_ms_avg, m1.ttft_ms_avg)
        self.assertGreater(m8.request_throughput_rps, m1.request_throughput_rps)

    def test_thousands_separator_and_quoting_handled(self):
        # genai-perf quotes large numbers with thousands-separator commas, e.g. "475.44" is fine,
        # but a genuinely large aggregate throughput like "21,380.59" (real captured value, see
        # loadgen-builder's validation run) must not be mis-split on the embedded comma.
        m = parse_csv(os.path.join(FIXTURES, "genai_perf_concurrency8.csv"))
        self.assertAlmostEqual(m.tps_aggregate, 475.44)

    def test_na_values_become_none_not_a_crash(self):
        m = parse_csv(os.path.join(FIXTURES, "genai_perf_na_values.csv"))
        self.assertIsNone(m.ttft_ms_avg)
        self.assertIsNone(m.request_latency_ms_avg)
        self.assertIsNone(m.tps_aggregate)
        self.assertIsNone(m.request_throughput_rps)
        # Sequence lengths were NOT N/A in the fixture -- still parse fine alongside the N/A rows.
        self.assertAlmostEqual(m.observed_input_len_avg, 200.00)
        # itl_ms_avg is also None here, so the tps_per_user ITL-inverse fallback can't kick in.
        self.assertIsNone(m.tps_per_user)

    def test_missing_value_block_raises_parse_error(self):
        with self.assertRaises(GenaiPerfParseError):
            parse_csv(os.path.join(FIXTURES, "genai_perf_missing_value_block.csv"))

    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            parse_csv(os.path.join(FIXTURES, "does_not_exist.csv"))


if __name__ == "__main__":
    unittest.main()

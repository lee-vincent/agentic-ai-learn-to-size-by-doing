"""tests/test_harness_parser.py -- agent-harness summary.json parsing."""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from experiment_cli.harness_parser import HarnessParseError, parse_summary

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


class TestHarnessParser(unittest.TestCase):
    def test_parses_summary_fixture(self):
        m = parse_summary(os.path.join(FIXTURES, "harness_summary.json"))
        self.assertAlmostEqual(m.tat_s_avg, 2.734)
        self.assertAlmostEqual(m.tat_s_p50, 2.601)
        self.assertAlmostEqual(m.tat_s_p90, 3.882)
        self.assertAlmostEqual(m.tat_s_min, 1.802)
        self.assertAlmostEqual(m.tat_s_max, 4.115)
        self.assertEqual(m.num_sessions_completed, 12)
        self.assertEqual(m.num_sessions_ok, 12)

    def test_single_session_p90_none_not_a_crash(self):
        # run_harness.py itself only computes p90 when len(tats) >= 2 -- confirm None survives
        # parsing without raising (results.py is responsible for the 0.0 coercion downstream).
        m = parse_summary(os.path.join(FIXTURES, "harness_summary_single_session.json"))
        self.assertIsNone(m.tat_s_p90)
        self.assertAlmostEqual(m.tat_s_avg, 2.1)

    def test_malformed_json_raises(self):
        bad_path = os.path.join(FIXTURES, "_bad_summary.json")
        with open(bad_path, "w", encoding="utf-8") as f:
            f.write("{not valid json")
        try:
            with self.assertRaises(HarnessParseError):
                parse_summary(bad_path)
        finally:
            os.remove(bad_path)

    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            parse_summary(os.path.join(FIXTURES, "does_not_exist.json"))


if __name__ == "__main__":
    unittest.main()

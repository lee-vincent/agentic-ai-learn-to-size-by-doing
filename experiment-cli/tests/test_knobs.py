"""tests/test_knobs.py -- the generic knob abstraction."""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from experiment_cli.knobs import KnobError, get_knob


class TestKnobs(unittest.TestCase):
    def test_concurrency_knob_applies(self):
        knob = get_knob("concurrency")
        self.assertFalse(knob.restart_required)
        updates = knob.apply(8, {"concurrency": 4})
        self.assertEqual(updates, {"concurrency": 8})

    def test_concurrency_knob_rejects_non_integer(self):
        knob = get_knob("concurrency")
        with self.assertRaises(KnobError):
            knob.apply("not-a-number", {})

    def test_isl_osl_knob_applies(self):
        knob = get_knob("isl_osl")
        updates = knob.apply("2000:64", {})
        self.assertEqual(updates, {"isl_mean": 2000, "osl_mean": 64})

    def test_reasoning_effort_knob_validates_choices(self):
        knob = get_knob("reasoning_effort")
        self.assertEqual(knob.apply("high", {}), {"reasoning_effort": "high"})
        with self.assertRaises(KnobError):
            knob.apply("extreme", {})

    def test_precision_knob_requires_restart_and_has_no_apply(self):
        knob = get_knob("precision")
        self.assertTrue(knob.restart_required)
        self.assertIsNone(knob.apply)

    def test_kv_cache_strategy_and_decoding_algorithm_also_require_restart(self):
        self.assertTrue(get_knob("kv_cache_strategy").restart_required)
        self.assertTrue(get_knob("decoding_algorithm").restart_required)

    def test_unknown_knob_raises(self):
        with self.assertRaises(KnobError):
            get_knob("not_a_real_knob")


if __name__ == "__main__":
    unittest.main()

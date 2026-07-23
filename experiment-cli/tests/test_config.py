"""tests/test_config.py -- sweep config loading (JSON + YAML-lite) and defaulting."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from experiment_cli.config import ConfigError, load_sweep_config

EXPERIMENT_CLI_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


class TestConfig(unittest.TestCase):
    def test_loads_checked_in_json_sweep_config(self):
        cfg = load_sweep_config(os.path.join(EXPERIMENT_CLI_DIR, "sweeps", "concurrency_1_8.json"))
        self.assertEqual(cfg["knob"], "concurrency")
        self.assertEqual(cfg["values"], [1, 8])
        self.assertEqual(cfg["fixed"]["precision"], "fp8")
        self.assertEqual(cfg["fixed"]["reasoning_effort"], "off")

    def test_loads_checked_in_yaml_lite_sweep_config(self):
        cfg = load_sweep_config(
            os.path.join(EXPERIMENT_CLI_DIR, "sweeps", "reasoning_effort_off_high.yaml"))
        self.assertEqual(cfg["knob"], "reasoning_effort")
        self.assertEqual(cfg["values"], ["off", "high"])
        self.assertEqual(cfg["fixed"]["concurrency"], 4)

    def test_missing_required_keys_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bad.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"knob": "concurrency"}, f)  # no 'values'
            with self.assertRaises(ConfigError):
                load_sweep_config(path)

    def test_defaults_applied_when_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "minimal.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"knob": "concurrency", "values": [1, 8]}, f)
            cfg = load_sweep_config(path)
            self.assertEqual(cfg["model"], "Qwen/Qwen3.6-27B-FP8")
            self.assertEqual(cfg["prometheus_url"], "http://localhost:9090")
            self.assertEqual(cfg["harness_concurrency_cap"], 16)


if __name__ == "__main__":
    unittest.main()

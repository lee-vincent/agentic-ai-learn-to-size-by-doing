"""Unit tests for AgentConfig's file < env < CLI precedence chain. No network involved."""
import argparse
import json
import os
import tempfile
import unittest

from agent.config import (
    AgentConfig,
    config_from_namespace,
    reasoning_extra_body,
)


class TestReasoningExtraBody(unittest.TestCase):
    def test_off_disables_thinking(self):
        self.assertEqual(
            reasoning_extra_body("off"), {"chat_template_kwargs": {"enable_thinking": False}}
        )

    def test_effort_levels_enable_thinking_and_pass_effort(self):
        for effort in ("low", "medium", "high"):
            body = reasoning_extra_body(effort)
            self.assertEqual(body["chat_template_kwargs"], {"enable_thinking": True})
            self.assertEqual(body["reasoning_effort"], effort)

    def test_case_insensitive(self):
        self.assertEqual(
            reasoning_extra_body("OFF"), {"chat_template_kwargs": {"enable_thinking": False}}
        )

    def test_invalid_effort_raises(self):
        with self.assertRaises(ValueError):
            reasoning_extra_body("extreme")


class TestAgentConfigDefaults(unittest.TestCase):
    def test_defaults_validate(self):
        cfg = AgentConfig()
        cfg.validate()  # should not raise
        self.assertEqual(cfg.model, "Qwen/Qwen3.6-27B-FP8")
        self.assertTrue(cfg.base_url.startswith("http"))

    def test_redacted_dict_masks_api_key(self):
        cfg = AgentConfig(api_key="super-secret")
        d = cfg.redacted_dict()
        self.assertNotIn("super-secret", json.dumps(d))

    def test_invalid_reasoning_effort_fails_validation(self):
        cfg = AgentConfig(reasoning_effort="nonsense")
        with self.assertRaises(ValueError):
            cfg.validate()

    def test_negative_max_tool_turns_fails_validation(self):
        cfg = AgentConfig(max_tool_turns=-1)
        with self.assertRaises(ValueError):
            cfg.validate()


class TestFromEnv(unittest.TestCase):
    def test_env_overrides_defaults_with_type_coercion(self):
        env = {
            "AGENT_BASE_URL": "http://example.invalid:8000/v1",
            "AGENT_MODEL": "some-other-model",
            "AGENT_TEMPERATURE": "0.2",
            "AGENT_MAX_TOOL_TURNS": "3",
            "AGENT_REASONING_EFFORT": "high",
        }
        cfg = AgentConfig.from_env(env=env)
        self.assertEqual(cfg.base_url, "http://example.invalid:8000/v1")
        self.assertEqual(cfg.model, "some-other-model")
        self.assertIsInstance(cfg.temperature, float)
        self.assertEqual(cfg.temperature, 0.2)
        self.assertIsInstance(cfg.max_tool_turns, int)
        self.assertEqual(cfg.max_tool_turns, 3)
        self.assertEqual(cfg.reasoning_effort, "high")

    def test_invalid_env_reasoning_effort_raises_at_load_time(self):
        with self.assertRaises(ValueError):
            AgentConfig.from_env(env={"AGENT_REASONING_EFFORT": "nonsense"})

    def test_config_file_layer(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"model": "from-file-model", "temperature": 0.1}, f)
            path = f.name
        try:
            cfg = AgentConfig.from_env(env={}, config_file=path)
            self.assertEqual(cfg.model, "from-file-model")
            self.assertEqual(cfg.temperature, 0.1)
        finally:
            os.unlink(path)

    def test_config_file_rejects_unknown_keys(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"totally_not_a_field": 1}, f)
            path = f.name
        try:
            with self.assertRaises(ValueError):
                AgentConfig.from_env(env={}, config_file=path)
        finally:
            os.unlink(path)

    def test_env_overrides_config_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"model": "from-file-model"}, f)
            path = f.name
        try:
            cfg = AgentConfig.from_env(env={"AGENT_MODEL": "from-env-model"}, config_file=path)
            self.assertEqual(cfg.model, "from-env-model")
        finally:
            os.unlink(path)


class TestPrecedenceChainWithCli(unittest.TestCase):
    def _namespace(self, **overrides):
        # Mirrors what argparse would hand back: every AgentConfig field defaults to None
        # (see add_config_arguments), plus whatever the caller explicitly set.
        base = {"config": None}
        from agent.config import CONFIG_FIELDS

        for field_name in CONFIG_FIELDS:
            base.setdefault(field_name, None)
        base.update(overrides)
        return argparse.Namespace(**base)

    def test_cli_overrides_env_and_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"model": "from-file-model"}, f)
            path = f.name
        try:
            args = self._namespace(config=path, model="from-cli-model")
            cfg = config_from_namespace(args, env={"AGENT_MODEL": "from-env-model"})
            self.assertEqual(cfg.model, "from-cli-model")
        finally:
            os.unlink(path)

    def test_unset_cli_flags_fall_through(self):
        args = self._namespace()
        cfg = config_from_namespace(args, env={"AGENT_MODEL": "from-env-model"})
        self.assertEqual(cfg.model, "from-env-model")
        # base_url wasn't touched by env or CLI -> built-in default survives.
        self.assertEqual(cfg.base_url, AgentConfig().base_url)

    def test_extra_body_cli_is_parsed_as_json(self):
        args = self._namespace(extra_body='{"seed": 7}')
        cfg = config_from_namespace(args, env={})
        self.assertEqual(cfg.extra_body, {"seed": 7})


if __name__ == "__main__":
    unittest.main()

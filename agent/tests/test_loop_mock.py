"""End-to-end test of the multi-step tool-calling loop against the bundled MockVLLMServer stub.

This proves the *loop mechanics* work (multi-turn message threading, tool dispatch and result
feeding, per-session JSONL logging with TAT timestamps, the reasoning-effort request field, and
the fallback-on-400 retry path) without a live vLLM endpoint. It does NOT prove that a real
Qwen3.6-27B model actually chooses to call these tools correctly -- that needs the live endpoint;
see agent/README.md.
"""
import json
import os
import tempfile
import unittest

from agent.config import AgentConfig
from agent.loop import Agent
from agent.mock_server import MockVLLMServer


class TestMultiStepToolLoop(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.log_dir = self._tmpdir.name

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_full_session_calls_both_tools_and_reaches_final_answer(self):
        with MockVLLMServer() as srv:
            config = AgentConfig(
                base_url=srv.base_url,
                model=srv.model_id,
                reasoning_effort="high",
                log_dir=self.log_dir,
                max_tool_turns=6,
            )
            agent = Agent(config)
            result = agent.run_session("What is 12 * 7? Also, how much VRAM does the L40S have?")

        self.assertEqual(result.status, "ok")
        self.assertIsNone(result.error)
        # Three model calls: tool_calls(calculator) -> tool_calls(kb_lookup) -> stop.
        self.assertEqual(result.num_model_calls, 3)
        self.assertEqual(result.num_tool_calls, 2)
        self.assertGreater(result.tat_seconds, 0)
        self.assertIn("84", result.final_text)
        self.assertIn("44.7", result.final_text)

        tool_names = [
            step["tool_calls"][0]["function"]["name"]
            for step in result.transcript
            if step.get("tool_calls")
        ]
        self.assertEqual(tool_names, ["calculator", "kb_lookup"])

        # reasoning_content should be present on the tool-calling turns since reasoning_effort="high".
        reasoning_turns = [s for s in result.transcript if s.get("reasoning_content")]
        self.assertGreaterEqual(len(reasoning_turns), 1)

        # Context should grow across turns (more messages sent each successive request).
        self.assertTrue(os.path.exists(result.log_path))
        request_message_counts = [len(req["messages"]) for req in srv.request_log]
        self.assertEqual(request_message_counts, sorted(request_message_counts))
        self.assertLess(request_message_counts[0], request_message_counts[-1])

    def test_session_log_contains_client_side_tat_timestamps(self):
        with MockVLLMServer() as srv:
            config = AgentConfig(base_url=srv.base_url, model=srv.model_id, log_dir=self.log_dir)
            agent = Agent(config)
            result = agent.run_session("What is 12 * 7?")

        with open(result.log_path, "r", encoding="utf-8") as f:
            events = [json.loads(line) for line in f]

        starts = [e for e in events if e["event"] == "session_start"]
        ends = [e for e in events if e["event"] == "session_end"]
        self.assertEqual(len(starts), 1)
        self.assertEqual(len(ends), 1)
        self.assertIn("submitted_at", starts[0])
        self.assertIn("submitted_at_epoch", starts[0])
        self.assertIn("completed_at", ends[0])
        self.assertIn("completed_at_epoch", ends[0])
        self.assertAlmostEqual(
            ends[0]["completed_at_epoch"] - starts[0]["submitted_at_epoch"],
            result.tat_seconds,
            places=3,
        )
        self.assertGreater(ends[0]["tat_seconds"], 0)

        index_path = os.path.join(self.log_dir, "sessions_index.jsonl")
        self.assertTrue(os.path.exists(index_path))
        with open(index_path, "r", encoding="utf-8") as f:
            index_events = [json.loads(line) for line in f]
        self.assertTrue(any(e["event"] == "session_end" for e in index_events))

    def test_reasoning_off_sends_enable_thinking_false_and_no_reasoning_content(self):
        with MockVLLMServer() as srv:
            config = AgentConfig(
                base_url=srv.base_url, model=srv.model_id, reasoning_effort="off",
                log_dir=self.log_dir,
            )
            agent = Agent(config)
            result = agent.run_session("What is 12 * 7?")

        self.assertEqual(result.status, "ok")
        self.assertTrue(
            all(not step.get("reasoning_content") for step in result.transcript)
        )
        first_request = srv.request_log[0]
        self.assertEqual(first_request["chat_template_kwargs"]["enable_thinking"], False)
        self.assertNotIn("reasoning_effort", first_request)

    def test_max_tool_turns_safety_cap(self):
        with MockVLLMServer() as srv:
            config = AgentConfig(
                base_url=srv.base_url, model=srv.model_id, max_tool_turns=0, log_dir=self.log_dir
            )
            agent = Agent(config)
            result = agent.run_session("What is 12 * 7?")

        self.assertEqual(result.status, "max_turns_exceeded")
        self.assertEqual(result.num_model_calls, 1)
        self.assertEqual(result.num_tool_calls, 1)  # the turn-0 calculator call still executes


class TestReasoningEffortFallback(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.log_dir = self._tmpdir.name

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_falls_back_when_server_rejects_reasoning_effort_field(self):
        with MockVLLMServer(strict_reasoning_effort=True) as srv:
            config = AgentConfig(
                base_url=srv.base_url,
                model=srv.model_id,
                reasoning_effort="high",
                log_dir=self.log_dir,
                max_tool_turns=6,
            )
            agent = Agent(config)
            result = agent.run_session("What is 12 * 7? Also, how much VRAM does the L40S have?")

        self.assertEqual(result.status, "ok")
        self.assertIn("84", result.final_text)
        # The server should have seen the retried request (without reasoning_effort) succeed,
        # so the second logged request per turn has enable_thinking still True but no
        # reasoning_effort key.
        successful_requests = [
            req for req in srv.request_log if "reasoning_effort" not in req
        ]
        self.assertGreater(len(successful_requests), 0)
        self.assertTrue(
            all(req["chat_template_kwargs"]["enable_thinking"] is True for req in successful_requests)
        )


if __name__ == "__main__":
    unittest.main()

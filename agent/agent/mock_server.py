"""A local stub of vLLM's OpenAI-compatible /v1/chat/completions endpoint.

This is a TEST DOUBLE, not a substitute for the real verification described in agent/README.md.
It exists so the tool-calling loop's *mechanics* (multi-turn message threading, tool dispatch,
per-session logging, the reasoning-effort request field, the 400-fallback path) can be exercised
and unit-tested without a live GPU/vLLM endpoint, which agent-builder does not have access to in
this environment (see agent/README.md "Verification: local vs. live endpoint").

Scripted behavior (deterministic, keyed off how many `role: tool` messages are already in the
incoming request's `messages`, not off any real NLU):
  - turn 0 (no tool results yet):   respond with a `calculator` tool call
  - turn 1 (one tool result seen):  respond with a `kb_lookup` tool call
  - turn 2+ (two+ tool results):    respond with a plain-text final answer, finish_reason "stop"

If `strict_reasoning_effort=True`, any request that includes a top-level `reasoning_effort`
field gets a 400 response mimicking a server that doesn't recognize that field — used to test
Agent._call_model()'s fallback-and-retry path.
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


def _tool_call(call_id: str, name: str, arguments: dict) -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def _count_tool_messages(messages: list[dict]) -> int:
    return sum(1 for m in messages if m.get("role") == "tool")


class _Handler(BaseHTTPRequestHandler):
    server: "MockVLLMServer"  # type: ignore[assignment]

    def log_message(self, fmt: str, *args: Any) -> None:  # silence default stderr logging
        pass

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - http.server naming convention
        if self.path == "/health":
            self._send_json(200, {"status": "ok"})
            return
        if self.path.rstrip("/") == "/v1/models":
            self._send_json(
                200,
                {
                    "object": "list",
                    "data": [{"id": self.server.model_id, "object": "model"}],
                },
            )
            return
        self._send_json(404, {"error": f"no such GET route: {self.path}"})

    def do_POST(self) -> None:  # noqa: N802
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._send_json(404, {"error": f"no such POST route: {self.path}"})
            return

        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            request_body = json.loads(raw)
        except json.JSONDecodeError as exc:
            self._send_json(400, {"error": f"invalid JSON body: {exc}"})
            return

        self.server.request_log.append(request_body)

        if self.server.strict_reasoning_effort and "reasoning_effort" in request_body:
            self._send_json(
                400,
                {
                    "error": {
                        "message": "Unrecognized request argument supplied: reasoning_effort",
                        "type": "invalid_request_error",
                    }
                },
            )
            return

        time.sleep(self.server.artificial_latency_seconds)  # make TAT/latency non-zero & visible

        model = request_body.get("model", self.server.model_id)
        messages = request_body.get("messages", [])
        enable_thinking = bool(
            request_body.get("chat_template_kwargs", {}).get("enable_thinking", False)
        )
        n_tool_results = _count_tool_messages(messages)

        reasoning_content = (
            "Breaking the task down: first get an exact number from the calculator tool, then "
            "check the knowledge base for the fact being asked about."
            if enable_thinking
            else None
        )

        message: dict[str, Any] = {"role": "assistant"}
        if reasoning_content:
            message["reasoning_content"] = reasoning_content

        if n_tool_results == 0:
            message["content"] = None
            message["tool_calls"] = [_tool_call("call_calc_1", "calculator", {"expression": "12 * 7"})]
            finish_reason = "tool_calls"
        elif n_tool_results == 1:
            message["content"] = None
            message["tool_calls"] = [
                _tool_call("call_kb_1", "kb_lookup", {"query": "L40S VRAM capacity"})
            ]
            finish_reason = "tool_calls"
        else:
            message["content"] = (
                "12 * 7 = 84. Per the lab's knowledge base, the L40S GPU has 44.7 GiB of VRAM."
            )
            message["tool_calls"] = None
            finish_reason = "stop"

        prompt_tokens = 40 + 20 * len(messages)
        completion_tokens = 30 + 10 * n_tool_results
        response = {
            "id": f"chatcmpl-stub-{self.server.request_count}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }
        self.server.request_count += 1
        self._send_json(200, response)


class MockVLLMServer:
    """Context manager wrapping a ThreadingHTTPServer running `_Handler` on a random localhost
    port. Usage:

        with MockVLLMServer() as srv:
            config = AgentConfig(base_url=srv.base_url, model=srv.model_id, ...)
            agent = Agent(config)
            result = agent.run_session("some task")
    """

    def __init__(
        self,
        model_id: str = "mock-qwen3.6-27b",
        strict_reasoning_effort: bool = False,
        artificial_latency_seconds: float = 0.02,
    ):
        self.model_id = model_id
        self.strict_reasoning_effort = strict_reasoning_effort
        self.artificial_latency_seconds = artificial_latency_seconds
        self.request_log: list[dict] = []
        self.request_count = 0
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> "MockVLLMServer":
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        # Stash config the handler reads via `self.server.*`.
        self._httpd.model_id = self.model_id  # type: ignore[attr-defined]
        self._httpd.strict_reasoning_effort = self.strict_reasoning_effort  # type: ignore[attr-defined]
        self._httpd.artificial_latency_seconds = self.artificial_latency_seconds  # type: ignore[attr-defined]
        self._httpd.request_log = self.request_log  # type: ignore[attr-defined]
        self._httpd.request_count = self.request_count  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()

    @property
    def base_url(self) -> str:
        assert self._httpd is not None, "call start() (or use as a context manager) first"
        return f"http://127.0.0.1:{self._httpd.server_port}/v1"

    def __enter__(self) -> "MockVLLMServer":
        return self.start()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()

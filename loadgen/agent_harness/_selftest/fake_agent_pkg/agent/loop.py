"""
SELF-TEST SCAFFOLDING ONLY -- not the real agent/.

Minimal stand-in Agent.run_session() that makes one real (non-streaming) HTTP POST to
{base_url}/chat/completions against a stub OpenAI-compatible server (see
loadgen/scripts/stub_vllm_server.py), so run_harness.py's ImportEngine has something real to call
end to end. Does not implement an actual tool-calling loop (the stub server never returns
tool_calls) -- that behavior is agent-builder's responsibility to validate in agent/'s own test
suite, not something this harness's self-test needs to fake convincingly.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Optional

from .config import AgentConfig


@dataclass
class SessionResult:
    session_id: str
    task: str
    final_text: Optional[str]
    status: str
    error: Optional[str]
    tat_seconds: float
    num_model_calls: int
    num_tool_calls: int
    total_prompt_tokens: int
    total_completion_tokens: int
    transcript: list = field(default_factory=list)
    log_path: Optional[str] = None


class Agent:
    def __init__(self, config: AgentConfig):
        self.config = config.validate()

    def run_session(self, task: str, session_id: Optional[str] = None) -> SessionResult:
        session_id = session_id or str(uuid.uuid4())
        start = time.monotonic()
        status = "ok"
        error = None
        final_text = None
        prompt_tokens = 0
        completion_tokens = 0
        try:
            url = self.config.base_url.rstrip("/") + "/chat/completions"
            body = json.dumps({
                "model": self.config.model,
                "messages": [
                    {"role": "system", "content": self.config.system_prompt},
                    {"role": "user", "content": task},
                ],
                "max_tokens": self.config.max_tokens,
                "stream": False,
            }).encode()
            req = urllib.request.Request(
                url, data=body, headers={"Content-Type": "application/json"}, method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.config.request_timeout) as resp:
                payload = json.loads(resp.read())
            final_text = payload["choices"][0]["message"]["content"]
            usage = payload.get("usage", {})
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)
        except (urllib.error.URLError, KeyError, json.JSONDecodeError) as exc:
            status = "error"
            error = f"{type(exc).__name__}: {exc}"
        tat_seconds = time.monotonic() - start
        return SessionResult(
            session_id=session_id, task=task, final_text=final_text, status=status,
            error=error, tat_seconds=tat_seconds, num_model_calls=1, num_tool_calls=0,
            total_prompt_tokens=prompt_tokens, total_completion_tokens=completion_tokens,
            log_path=None,
        )

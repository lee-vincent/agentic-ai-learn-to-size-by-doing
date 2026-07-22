"""
SELF-TEST SCAFFOLDING ONLY -- not the real agent/.

A minimal black-box-faithful stand-in for the assumed agent/config.py contract (see
loadgen/agent_harness/README.md "Assumed agent/ interface"), just enough of it for
run_harness.py's adapter.py (both CliEngine and ImportEngine) to exercise for real against a
stub HTTP endpoint. Delete/ignore this whole _selftest/ tree once the real agent/ lands.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AgentConfig:
    base_url: str = "http://localhost:8000/v1"
    api_key: str = "EMPTY"
    model: str = "Qwen/Qwen3.6-27B-FP8"
    reasoning_effort: str = "medium"
    temperature: float = 0.7
    top_p: float = 0.9
    max_tokens: int = 256
    max_tool_turns: int = 6
    request_timeout: float = 120.0
    log_dir: str = "./logs"
    system_prompt: str = "You are a fake self-test agent."
    extra_body: dict = field(default_factory=dict)

    def validate(self) -> "AgentConfig":
        if not self.base_url:
            raise ValueError("base_url must not be empty")
        if not self.model:
            raise ValueError("model must not be empty")
        return self

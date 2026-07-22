"""agent — a real tool-calling agent for the GPU Sizing Lab (Phase 3, SPEC.md / GOALS.md).

Targets a vLLM OpenAI-compatible endpoint (default: Qwen3.6-27B served per containers/vllm/).
Two tools at minimum (calculator, kb_lookup), a multi-step tool-calling loop, configurable
base_url/model/reasoning-effort, and a per-session JSONL log with client-side timestamps at
request submission and final-token delivery for Turnaround Time (TAT) measurement.

See agent/README.md for the config interface, tool schemas, and what has/hasn't been verified
against a live vLLM endpoint.
"""

from .config import AgentConfig
from .loop import Agent, SessionResult

__all__ = ["AgentConfig", "Agent", "SessionResult"]

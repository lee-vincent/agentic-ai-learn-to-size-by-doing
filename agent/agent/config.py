"""Configuration for the agent — layered from (lowest to highest precedence):

    built-in defaults  <  config file (JSON)  <  environment variables (AGENT_*)  <  CLI flags

This mirrors the "config, not hardcoded" requirement in SPEC.md / GOALS.md Phase 3: base_url,
model, and reasoning_effort must all be changeable without touching code. Three override
mechanisms are supported (env / CLI / config file), same as the brief asked for.

Env vars:  AGENT_BASE_URL, AGENT_API_KEY, AGENT_MODEL, AGENT_REASONING_EFFORT,
           AGENT_TEMPERATURE, AGENT_TOP_P, AGENT_MAX_TOKENS, AGENT_MAX_TOOL_TURNS,
           AGENT_REQUEST_TIMEOUT, AGENT_LOG_DIR, AGENT_SYSTEM_PROMPT, AGENT_EXTRA_BODY,
           AGENT_CONFIG_FILE (path to a JSON config file, same effect as --config)

CLI flags: --base-url --api-key --model --reasoning-effort --temperature --top-p --max-tokens
           --max-tool-turns --request-timeout --log-dir --system-prompt --extra-body --config

Config file: JSON object whose keys are the same field names (see config/agent.config.example.json).
"""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

ENV_PREFIX = "AGENT_"

VALID_REASONING_EFFORTS = ("off", "low", "medium", "high")

DEFAULT_SYSTEM_PROMPT = (
    "You are a careful assistant with access to two tools:\n"
    "- `calculator`: evaluate an arithmetic expression exactly (use this instead of doing math "
    "in your head whenever precision matters).\n"
    "- `kb_lookup`: search a small local knowledge base about this GPU-serving lab (GPU/model "
    "specs, vLLM flags, metric definitions) by keyword query.\n"
    "Call a tool whenever the user's question needs precise arithmetic or a specific fact you "
    "are not certain of. Once you have what you need, give a direct final answer in plain text "
    "with no further tool calls."
)

# Field name -> caster used when a value arrives as a raw string (env vars, and CLI values that
# argparse hands back as strings for fields where we didn't set an explicit `type=`).
_FIELD_CASTERS = {
    "temperature": float,
    "top_p": float,
    "max_tokens": int,
    "max_tool_turns": int,
    "request_timeout": float,
    "extra_body": lambda raw: raw if isinstance(raw, dict) else json.loads(raw),
}

# Every field that participates in the file/env/CLI layering (i.e. everything except things that
# are computed or session-specific).
CONFIG_FIELDS = (
    "base_url",
    "api_key",
    "model",
    "reasoning_effort",
    "temperature",
    "top_p",
    "max_tokens",
    "max_tool_turns",
    "request_timeout",
    "log_dir",
    "system_prompt",
    "extra_body",
)


def reasoning_extra_body(effort: str) -> dict:
    """Translate the reasoning-effort knob into the vLLM/OpenAI request-level fields that turn
    Qwen3.6's "thinking mode" on/off and (best-effort) signal a graded effort level.

    Confirmed (per containers/vllm/README.md, current Qwen3.6 model card, and the vLLM
    `--reasoning-parser qwen3` docs): thinking mode is toggled per-request via
    `chat_template_kwargs.enable_thinking` (bool). "off" maps to False; anything else maps to True.

    NOT independently confirmed against a live endpoint: whether vLLM/Qwen3.6 honors a *graded*
    `reasoning_effort` value (low/medium/high) the way OpenAI's reasoning-model API does, or simply
    ignores/errors on that extra field. We pass it through as a best-effort signal alongside
    enable_thinking; Agent._call_model() catches a 400 that specifically complains about this field
    and retries once without it (see loop.py), so an unsupported server degrades gracefully to
    plain thinking-on/off rather than failing the whole request.
    """
    effort = (effort or "off").strip().lower()
    if effort not in VALID_REASONING_EFFORTS:
        raise ValueError(
            f"reasoning_effort must be one of {VALID_REASONING_EFFORTS}, got {effort!r}"
        )
    if effort == "off":
        return {"chat_template_kwargs": {"enable_thinking": False}}
    return {"chat_template_kwargs": {"enable_thinking": True}, "reasoning_effort": effort}


@dataclass
class AgentConfig:
    # vLLM OpenAI-compatible endpoint. Default matches containers/vllm's local docker-compose
    # port mapping; override for the real EC2 host (e.g. http://<host>:8000/v1).
    base_url: str = "http://localhost:8000/v1"
    # vLLM does not check this, but the openai SDK requires a non-empty string.
    api_key: str = "EMPTY"
    # Must match --served-model-name on the server (see containers/vllm/entrypoint.sh).
    model: str = "Qwen/Qwen3.6-27B-FP8"
    # SPEC.md knob: "off" disables thinking mode; low/medium/high enable it (see
    # reasoning_extra_body() above for exactly what each maps to on the wire).
    reasoning_effort: str = "medium"
    temperature: float = 0.7
    top_p: float = 0.9
    max_tokens: int = 1024
    # Safety cap on the tool-calling loop so a misbehaving model can't loop forever.
    max_tool_turns: int = 6
    request_timeout: float = 120.0
    log_dir: str = "./logs"
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    # Escape hatch merged into every request body (e.g. {"seed": 0}); reasoning fields from
    # reasoning_extra_body() are applied on top of this, not overwritten by it.
    extra_body: dict = field(default_factory=dict)

    def validate(self) -> "AgentConfig":
        reasoning_extra_body(self.reasoning_effort)  # raises ValueError if invalid
        if not self.base_url:
            raise ValueError("base_url must not be empty")
        if not self.model:
            raise ValueError("model must not be empty")
        if self.max_tool_turns < 0:
            raise ValueError("max_tool_turns must be >= 0")
        if not isinstance(self.extra_body, dict):
            raise ValueError("extra_body must be a dict")
        return self

    def redacted_dict(self) -> dict:
        """Config snapshot safe to write into a log file (API key masked)."""
        d = asdict(self)
        if d.get("api_key"):
            d["api_key"] = "***redacted***"
        return d

    @classmethod
    def from_env(
        cls, env: Optional[dict] = None, config_file: Optional[str] = None
    ) -> "AgentConfig":
        """Layer built-in defaults < config file < environment variables. No CLI involved —
        this is the entry point a Python caller (e.g. loadgen-builder's harness) uses to get a
        config without going through argparse."""
        env = os.environ if env is None else env
        cfg = cls()

        path = config_file or env.get(f"{ENV_PREFIX}CONFIG_FILE")
        if path:
            for key, value in _load_config_file(path).items():
                setattr(cfg, key, value)

        for field_name in CONFIG_FIELDS:
            env_key = f"{ENV_PREFIX}{field_name.upper()}"
            if env_key in env:
                setattr(cfg, field_name, _coerce(field_name, env[env_key]))

        return cfg.validate()

    def apply_cli_overrides(self, args: argparse.Namespace) -> "AgentConfig":
        """Apply only the CLI flags the user actually passed (argparse leaves the rest as
        `None` — see add_config_arguments() below, where every flag defaults to None)."""
        for field_name in CONFIG_FIELDS:
            cli_val = getattr(args, field_name, None)
            if cli_val is not None:
                setattr(self, field_name, _coerce(field_name, cli_val))
        return self.validate()


def _coerce(field_name: str, raw: Any) -> Any:
    caster = _FIELD_CASTERS.get(field_name)
    if caster is None or not isinstance(raw, str):
        return raw
    return caster(raw)


def _load_config_file(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"config file {path} must contain a JSON object")
    unknown = set(data) - set(CONFIG_FIELDS)
    if unknown:
        raise ValueError(f"unknown keys in config file {path}: {sorted(unknown)}")
    return data


def add_config_arguments(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Add every AgentConfig flag onto `parser`, all defaulting to None so
    apply_cli_overrides() can tell "not passed" apart from "explicitly passed the default"."""
    g = parser.add_argument_group(
        "agent configuration (env: AGENT_*; file: --config path.json; CLI wins over both)"
    )
    g.add_argument("--config", metavar="PATH", default=None, help="JSON config file")
    g.add_argument("--base-url", dest="base_url", default=None)
    g.add_argument("--api-key", dest="api_key", default=None)
    g.add_argument("--model", dest="model", default=None)
    g.add_argument(
        "--reasoning-effort",
        dest="reasoning_effort",
        default=None,
        help=f"one of {VALID_REASONING_EFFORTS}",
    )
    g.add_argument("--temperature", dest="temperature", type=float, default=None)
    g.add_argument("--top-p", dest="top_p", type=float, default=None)
    g.add_argument("--max-tokens", dest="max_tokens", type=int, default=None)
    g.add_argument("--max-tool-turns", dest="max_tool_turns", type=int, default=None)
    g.add_argument("--request-timeout", dest="request_timeout", type=float, default=None)
    g.add_argument("--log-dir", dest="log_dir", default=None)
    g.add_argument("--system-prompt", dest="system_prompt", default=None)
    g.add_argument(
        "--extra-body",
        dest="extra_body",
        default=None,
        help="JSON object merged into every request body, e.g. '{\"seed\": 0}'",
    )
    return parser


def config_from_namespace(
    args: argparse.Namespace, env: Optional[dict] = None
) -> AgentConfig:
    """Full precedence chain: defaults < config file < env < CLI."""
    cfg = AgentConfig.from_env(env=env, config_file=getattr(args, "config", None))
    return cfg.apply_cli_overrides(args)


def load_config(argv: Optional[list] = None) -> AgentConfig:
    """Standalone convenience: parse just the config flags out of `argv` (default: sys.argv) and
    return the resulting AgentConfig. Unrecognized args (e.g. a caller's own --task) are ignored,
    so this composes fine when a bigger CLI (cli.py) has already added more arguments of its own —
    though cli.py itself calls config_from_namespace() directly against its own already-parsed
    namespace instead of re-parsing."""
    parser = argparse.ArgumentParser(add_help=False)
    add_config_arguments(parser)
    args, _unknown = parser.parse_known_args(argv)
    return config_from_namespace(args)

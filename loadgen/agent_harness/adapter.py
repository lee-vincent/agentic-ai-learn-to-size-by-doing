"""
loadgen/agent_harness/adapter.py

Adapter between this load harness and the Phase 3 `agent/` package. `agent/` is being built in a
parallel worktree and is NOT present here (confirmed empty at build time) -- per the build brief,
this codes against a documented, assumed interface rather than blocking on it. See
loadgen/agent_harness/README.md "Assumed agent/ interface" for the full contract and how to
reconcile at integration if the real implementation differs.

Two engines, selected via `--engine {cli,import}` on run_harness.py:

- CliEngine (default): shells out to
    python -m agent.cli --task TASK --base-url URL --model MODEL [--reasoning-effort E]
      [--max-tokens N] --session-id ID
  as a subprocess and parses its stdout. Most decoupled -- works as long as `python -m agent.cli`
  runs from `agent_cwd`, regardless of how/where `agent/` and its dependencies are installed.

- ImportEngine: `import agent.config` / `import agent.loop` directly (agent_cwd is inserted at
  the front of sys.path first) and calls `Agent(config).run_session(task, session_id=...)`
  in-process. Lower per-session overhead at high concurrency (no interpreter spawn); requires
  `agent/` to be importable, i.e. actually present under `agent_cwd`.

Both normalize to the same `NormalizedResult` regardless of which engine ran.
"""
from __future__ import annotations

import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class NormalizedResult:
    session_id: str
    task: str
    status: str  # "ok" | "max_turns_exceeded" | "error" | "timeout"
    error: Optional[str]
    tat_seconds: Optional[float]           # authoritative TAT, per the agent's own measurement
    harness_observed_seconds: float        # wall clock the harness itself measured (spawn-inclusive
                                            # for --engine cli; should ~match tat_seconds for --engine import)
    num_model_calls: Optional[int]
    num_tool_calls: Optional[int]
    prompt_tokens: Optional[int]
    completion_tokens: Optional[int]
    final_text: Optional[str]
    log_path: Optional[str]
    engine: str
    raw_stdout: Optional[str] = None
    raw_stderr: Optional[str] = None


class AgentInterfaceError(RuntimeError):
    """Raised when the assumed agent/ interface isn't satisfiable (missing package, unexpected
    output shape, etc.) -- kept distinct from a normal per-session "status: error" result so the
    harness can tell "agent/ isn't wired up right" apart from "one session failed for a real
    reason" and fail loudly rather than silently recording garbage rows."""


# --- CLI engine --------------------------------------------------------------------------------

# Matches the fixed "key:        value" lines this contract assumes cli.py prints per session
# (one session per invocation here -- run_harness.py always passes exactly one --task).
_LINE_RE = re.compile(r"^([a-zA-Z_ ]+?):\s+(.*)$")


def _parse_cli_stdout(stdout: str) -> dict:
    fields: dict[str, str] = {}
    for line in stdout.splitlines():
        m = _LINE_RE.match(line)
        if not m:
            continue
        key, value = m.group(1).strip(), m.group(2)
        fields[key] = value
    return fields


class CliEngine:
    def __init__(
        self,
        agent_cwd: str = ".",
        python_bin: Optional[str] = None,
        extra_args: Optional[list[str]] = None,
    ):
        self.agent_cwd = agent_cwd
        self.python_bin = python_bin or sys.executable
        self.extra_args = extra_args or []

    def run_session(
        self,
        task: str,
        base_url: str,
        model: str,
        session_id: str,
        reasoning_effort: Optional[str] = None,
        max_tokens: Optional[int] = None,
        timeout_seconds: float = 180.0,
    ) -> NormalizedResult:
        argv = [
            self.python_bin, "-m", "agent.cli",
            "--task", task,
            "--base-url", base_url,
            "--model", model,
            "--session-id", session_id,
        ]
        if reasoning_effort:
            argv += ["--reasoning-effort", reasoning_effort]
        if max_tokens:
            argv += ["--max-tokens", str(max_tokens)]
        argv += self.extra_args

        start = time.monotonic()
        try:
            proc = subprocess.run(
                argv, cwd=self.agent_cwd, capture_output=True, text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            elapsed = time.monotonic() - start
            return NormalizedResult(
                session_id=session_id, task=task, status="timeout",
                error=f"subprocess exceeded {timeout_seconds}s",
                tat_seconds=None, harness_observed_seconds=elapsed,
                num_model_calls=None, num_tool_calls=None,
                prompt_tokens=None, completion_tokens=None,
                final_text=None, log_path=None, engine="cli",
                raw_stdout=(exc.stdout or ""), raw_stderr=(exc.stderr or ""),
            )
        elapsed = time.monotonic() - start

        if proc.returncode not in (0, 1):
            # 0 = all sessions "ok"; 1 = cli.py's own convention for "at least one session in
            # this invocation did not end status=='ok'" (see the assumed interface doc) -- both
            # are a completed process we can try to parse. Anything else is a real crash.
            return NormalizedResult(
                session_id=session_id, task=task, status="error",
                error=f"agent.cli exited {proc.returncode}",
                tat_seconds=None, harness_observed_seconds=elapsed,
                num_model_calls=None, num_tool_calls=None,
                prompt_tokens=None, completion_tokens=None,
                final_text=None, log_path=None, engine="cli",
                raw_stdout=proc.stdout, raw_stderr=proc.stderr,
            )

        fields = _parse_cli_stdout(proc.stdout)
        if "session_id" not in fields:
            raise AgentInterfaceError(
                "agent.cli produced output that doesn't match the assumed 'key:  value' contract "
                f"(see README.md 'Assumed agent/ interface'). stdout was:\n{proc.stdout}"
            )

        status_field = fields.get("status", "")
        status = status_field.split(" (", 1)[0].strip() or "error"
        error_msg = None
        if "(" in status_field and status_field.endswith(")"):
            error_msg = status_field[status_field.index("(") + 1 : -1]

        def _f(key, caster):
            raw = fields.get(key)
            if raw is None:
                return None
            try:
                return caster(raw)
            except ValueError:
                return None

        return NormalizedResult(
            session_id=fields.get("session_id", session_id),
            task=task,
            status=status,
            error=error_msg,
            tat_seconds=_f("tat_seconds", float),
            harness_observed_seconds=elapsed,
            num_model_calls=_f("num_model_calls", int),
            num_tool_calls=_f("num_tool_calls", int),
            prompt_tokens=_f("prompt_tokens", int),
            completion_tokens=_f("completion_tokens", int),
            final_text=fields.get("final answer"),
            log_path=fields.get("log_path"),
            engine="cli",
            raw_stdout=proc.stdout,
            raw_stderr=proc.stderr,
        )


# --- Import engine ------------------------------------------------------------------------------

class ImportEngine:
    def __init__(self, agent_cwd: str = "."):
        self.agent_cwd = agent_cwd
        self._agent_config_mod = None
        self._agent_loop_mod = None

    def _ensure_imported(self):
        if self._agent_loop_mod is not None:
            return
        if self.agent_cwd not in sys.path:
            sys.path.insert(0, self.agent_cwd)
        try:
            import agent.config as agent_config  # type: ignore
            import agent.loop as agent_loop  # type: ignore
        except ModuleNotFoundError as exc:
            raise AgentInterfaceError(
                f"agent/ is not importable from agent_cwd={self.agent_cwd!r} ({exc}). "
                "Either check out agent/ there, or use --engine cli instead. See README.md "
                "'Assumed agent/ interface'."
            ) from exc
        self._agent_config_mod = agent_config
        self._agent_loop_mod = agent_loop

    def run_session(
        self,
        task: str,
        base_url: str,
        model: str,
        session_id: str,
        reasoning_effort: Optional[str] = None,
        max_tokens: Optional[int] = None,
        timeout_seconds: float = 180.0,
    ) -> NormalizedResult:
        self._ensure_imported()
        start = time.monotonic()
        try:
            config = self._agent_config_mod.AgentConfig()
            config.base_url = base_url
            config.model = model
            if reasoning_effort:
                config.reasoning_effort = reasoning_effort
            if max_tokens:
                config.max_tokens = max_tokens
            config.request_timeout = timeout_seconds
            config.validate()

            agent = self._agent_loop_mod.Agent(config)
            result = agent.run_session(task, session_id=session_id)
        except AgentInterfaceError:
            raise
        except Exception as exc:  # noqa: BLE001 - one session's failure must not crash the batch
            elapsed = time.monotonic() - start
            return NormalizedResult(
                session_id=session_id, task=task, status="error",
                error=f"{type(exc).__name__}: {exc}",
                tat_seconds=None, harness_observed_seconds=elapsed,
                num_model_calls=None, num_tool_calls=None,
                prompt_tokens=None, completion_tokens=None,
                final_text=None, log_path=None, engine="import",
            )
        elapsed = time.monotonic() - start

        return NormalizedResult(
            session_id=getattr(result, "session_id", session_id),
            task=task,
            status=getattr(result, "status", "unknown"),
            error=getattr(result, "error", None),
            tat_seconds=getattr(result, "tat_seconds", None),
            harness_observed_seconds=elapsed,
            num_model_calls=getattr(result, "num_model_calls", None),
            num_tool_calls=getattr(result, "num_tool_calls", None),
            prompt_tokens=getattr(result, "total_prompt_tokens", None),
            completion_tokens=getattr(result, "total_completion_tokens", None),
            final_text=getattr(result, "final_text", None),
            log_path=getattr(result, "log_path", None),
            engine="import",
        )


def make_engine(name: str, agent_cwd: str, python_bin: Optional[str] = None):
    if name == "cli":
        return CliEngine(agent_cwd=agent_cwd, python_bin=python_bin)
    if name == "import":
        return ImportEngine(agent_cwd=agent_cwd)
    raise ValueError(f"unknown engine {name!r}; expected 'cli' or 'import'")

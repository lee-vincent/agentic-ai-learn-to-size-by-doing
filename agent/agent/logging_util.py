"""Per-session JSONL logging.

SPEC.md requires Turnaround Time (TAT) — "total clock time from user submission to final token
delivery" — to be measured at the client/agent layer, since it spans the full round trip
including any agent reasoning/tool steps. This module is where that client-side timestamping
happens: every session gets a `session_start` event (timestamped the moment the task is
submitted, before the first model call) and a `session_end` event (timestamped the moment the
final answer is fully received, after the last model call/tool step), plus one event per model
call and per tool call in between so the full shape of the session (turns, tool calls, token
counts, per-step latency) is reconstructible from the log alone.

Each session writes to its own `session_<id>.jsonl` file, and every session_start/session_end
event is *also* appended to a shared `sessions_index.jsonl` so a load harness driving many
concurrent sessions (loadgen-builder) can scan TAT across all of them without opening every
per-session file individually.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Any


def now() -> tuple[float, str]:
    """Return (epoch_seconds, iso8601_utc) for the current instant, captured once so both
    representations of a single timestamp are guaranteed consistent."""
    epoch = time.time()
    iso = datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()
    return epoch, iso


class SessionLogger:
    """Writes one JSONL line per event for a single agent session."""

    def __init__(self, log_dir: str, session_id: str):
        self.session_id = session_id
        self.log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)
        self.session_path = os.path.join(log_dir, f"session_{session_id}.jsonl")
        self.index_path = os.path.join(log_dir, "sessions_index.jsonl")
        self._session_file = open(self.session_path, "a", encoding="utf-8")

    def log(self, event: dict[str, Any], also_index: bool = False) -> None:
        event = dict(event)
        event.setdefault("session_id", self.session_id)
        event.setdefault("logged_at", now()[1])
        line = json.dumps(event, default=str)
        self._session_file.write(line + "\n")
        self._session_file.flush()
        if also_index:
            with open(self.index_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")

    def close(self) -> None:
        try:
            self._session_file.close()
        except Exception:
            pass

    def __enter__(self) -> "SessionLogger":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

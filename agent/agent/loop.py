"""The multi-step tool-calling loop.

Agent.run_session() drives one conversation against the configured OpenAI-compatible endpoint:
call the model, and while it keeps asking for tool calls, execute them locally and feed the
results back as `role: tool` messages, until it returns a plain final answer (or the
`max_tool_turns` safety cap is hit). This is intentionally a plain Python loop, not a graph
framework — per the brief, realistic agentic traffic (multi-turn context growth, variable
output length, tool steps) matters more than loop sophistication.

Requires `--enable-auto-tool-choice --tool-call-parser qwen3_coder` on the vLLM server side —
without that, the server will never populate `message.tool_calls` and this loop will just see a
plain-text final answer on the first turn (which is not a bug in this file; check the serving
side first, per agent-builder's brief).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

import openai

from .config import AgentConfig, reasoning_extra_body
from .logging_util import SessionLogger, now
from .tools import TOOL_SCHEMAS, execute_tool_call


@dataclass
class SessionResult:
    session_id: str
    task: str
    final_text: Optional[str]
    status: str  # "ok" | "max_turns_exceeded" | "error"
    error: Optional[str]
    tat_seconds: float
    num_model_calls: int
    num_tool_calls: int
    total_prompt_tokens: int
    total_completion_tokens: int
    transcript: list[dict] = field(default_factory=list)
    messages: list[dict] = field(default_factory=list)
    log_path: Optional[str] = None


def _unknown_field_error(exc: "openai.BadRequestError", field_name: str) -> bool:
    """Best-effort detection of "server doesn't know this request field" so
    Agent._call_model() can retry once without it (see reasoning_extra_body()'s docstring for
    why `reasoning_effort` specifically needs this fallback)."""
    text = str(exc).lower()
    if field_name.lower() not in text:
        return False
    return any(
        marker in text
        for marker in ("unexpected", "unrecognized", "not permitted", "unknown", "invalid", "extra fields")
    )


def _tool_calls_for_input(tool_calls) -> list[dict]:
    return [tc.model_dump() for tc in tool_calls]


class Agent:
    """A tool-calling agent bound to one AgentConfig (endpoint + model + reasoning effort)."""

    def __init__(self, config: AgentConfig, client: Optional["openai.OpenAI"] = None):
        self.config = config.validate()
        self.client = client or openai.OpenAI(
            base_url=self.config.base_url,
            api_key=self.config.api_key,
            timeout=self.config.request_timeout,
        )

    # -- model call, with reasoning-field fallback -------------------------------------------

    def _call_model(self, messages: list[dict], extra_body_override: Optional[dict] = None):
        body = dict(self.config.extra_body)
        body.update(reasoning_extra_body(self.config.reasoning_effort))
        if extra_body_override:
            body.update(extra_body_override)

        try:
            response = self.client.chat.completions.create(
                model=self.config.model,
                messages=messages,
                tools=TOOL_SCHEMAS,
                tool_choice="auto",
                temperature=self.config.temperature,
                top_p=self.config.top_p,
                max_tokens=self.config.max_tokens,
                extra_body=body,
            )
            return response, body
        except openai.BadRequestError as exc:
            if "reasoning_effort" in body and _unknown_field_error(exc, "reasoning_effort"):
                fallback_body = {k: v for k, v in body.items() if k != "reasoning_effort"}
                response = self.client.chat.completions.create(
                    model=self.config.model,
                    messages=messages,
                    tools=TOOL_SCHEMAS,
                    tool_choice="auto",
                    temperature=self.config.temperature,
                    top_p=self.config.top_p,
                    max_tokens=self.config.max_tokens,
                    extra_body=fallback_body,
                )
                return response, fallback_body
            raise

    # -- the loop itself -----------------------------------------------------------------------

    def run_session(
        self,
        task: str,
        system_prompt: Optional[str] = None,
        session_id: Optional[str] = None,
        extra_body: Optional[dict] = None,
    ) -> SessionResult:
        session_id = session_id or str(uuid.uuid4())
        logger = SessionLogger(self.config.log_dir, session_id)

        submitted_epoch, submitted_iso = now()
        logger.log(
            {
                "event": "session_start",
                "submitted_at": submitted_iso,
                "submitted_at_epoch": submitted_epoch,
                "task": task,
                "config": self.config.redacted_dict(),
            },
            also_index=True,
        )

        messages: list[dict] = [
            {"role": "system", "content": system_prompt or self.config.system_prompt},
            {"role": "user", "content": task},
        ]
        transcript: list[dict] = []
        num_model_calls = 0
        num_tool_calls = 0
        total_prompt_tokens = 0
        total_completion_tokens = 0
        final_text: Optional[str] = None
        status = "ok"
        error_message: Optional[str] = None

        try:
            for turn in range(self.config.max_tool_turns + 1):
                call_start_epoch, call_start_iso = now()
                response, body_used = self._call_model(messages, extra_body)
                call_end_epoch, call_end_iso = now()
                num_model_calls += 1

                choice = response.choices[0]
                msg = choice.message
                finish_reason = choice.finish_reason
                usage = getattr(response, "usage", None)
                prompt_tokens = getattr(usage, "prompt_tokens", None) if usage else None
                completion_tokens = getattr(usage, "completion_tokens", None) if usage else None
                if isinstance(prompt_tokens, int):
                    total_prompt_tokens += prompt_tokens
                if isinstance(completion_tokens, int):
                    total_completion_tokens += completion_tokens
                reasoning_content = getattr(msg, "reasoning_content", None)
                tool_calls = list(msg.tool_calls) if msg.tool_calls else []

                logger.log(
                    {
                        "event": "model_call",
                        "turn": turn,
                        "request_sent_at": call_start_iso,
                        "response_received_at": call_end_iso,
                        "latency_seconds": call_end_epoch - call_start_epoch,
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "reasoning_chars": len(reasoning_content) if reasoning_content else 0,
                        "reasoning_effort_configured": self.config.reasoning_effort,
                        "num_tool_calls_requested": len(tool_calls),
                        "tool_call_names": [tc.function.name for tc in tool_calls],
                        "finish_reason": finish_reason,
                        "extra_body_sent": body_used,
                    }
                )
                transcript.append(
                    {
                        "role": "assistant",
                        "content": msg.content,
                        "reasoning_content": reasoning_content,
                        "tool_calls": _tool_calls_for_input(tool_calls) if tool_calls else None,
                    }
                )

                assistant_input_msg: dict[str, Any] = {"role": "assistant", "content": msg.content}
                if tool_calls:
                    assistant_input_msg["tool_calls"] = _tool_calls_for_input(tool_calls)
                messages.append(assistant_input_msg)

                if not tool_calls:
                    final_text = msg.content
                    break

                for tc in tool_calls:
                    tool_start_epoch, tool_start_iso = now()
                    result_str = execute_tool_call(tc.function.name, tc.function.arguments)
                    tool_end_epoch, _tool_end_iso = now()
                    num_tool_calls += 1
                    logger.log(
                        {
                            "event": "tool_call",
                            "turn": turn,
                            "tool_call_id": tc.id,
                            "tool_name": tc.function.name,
                            "arguments": tc.function.arguments,
                            "result": result_str,
                            "latency_seconds": tool_end_epoch - tool_start_epoch,
                            "executed_at": tool_start_iso,
                        }
                    )
                    messages.append(
                        {"role": "tool", "tool_call_id": tc.id, "content": result_str}
                    )
            else:
                # The for-loop ran out of turns without ever hitting `break`, i.e. the model
                # kept requesting tool calls all the way to max_tool_turns.
                status = "max_turns_exceeded"
                final_text = final_text or "(no final answer -- max_tool_turns exceeded)"
        except Exception as exc:  # noqa: BLE001 - deliberately broad: never let one session crash a batch
            status = "error"
            error_message = f"{type(exc).__name__}: {exc}"
            logger.log({"event": "error", "error": error_message})

        completed_epoch, completed_iso = now()
        tat_seconds = completed_epoch - submitted_epoch
        logger.log(
            {
                "event": "session_end",
                "submitted_at": submitted_iso,
                "submitted_at_epoch": submitted_epoch,
                "completed_at": completed_iso,
                "completed_at_epoch": completed_epoch,
                "tat_seconds": tat_seconds,
                "status": status,
                "error": error_message,
                "num_model_calls": num_model_calls,
                "num_tool_calls": num_tool_calls,
                "total_prompt_tokens": total_prompt_tokens,
                "total_completion_tokens": total_completion_tokens,
                "final_answer_chars": len(final_text) if final_text else 0,
            },
            also_index=True,
        )
        logger.close()

        return SessionResult(
            session_id=session_id,
            task=task,
            final_text=final_text,
            status=status,
            error=error_message,
            tat_seconds=tat_seconds,
            num_model_calls=num_model_calls,
            num_tool_calls=num_tool_calls,
            total_prompt_tokens=total_prompt_tokens,
            total_completion_tokens=total_completion_tokens,
            transcript=transcript,
            messages=messages,
            log_path=logger.session_path,
        )

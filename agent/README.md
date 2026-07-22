# agent — Phase 3 tool-calling agent (GPU Sizing Lab)

A real multi-step tool-calling agent targeting a vLLM OpenAI-compatible endpoint (default: the
`Qwen/Qwen3.6-27B-FP8` deployment built under `containers/vllm/`). Built per `SPEC.md` /
`GOALS.md` Phase 3: at minimum a calculator tool and a retrieval/lookup tool, a plain multi-step
loop (no graph framework needed), config-driven endpoint/model/reasoning-effort, and a per-session
log with client-side timestamps for Turnaround Time (TAT).

## What's here

| Path | Purpose |
|---|---|
| `agent/config.py` | `AgentConfig` dataclass + the file/env/CLI precedence chain. |
| `agent/tools.py` | The two tools (`calculator`, `kb_lookup`) — schemas + safe implementations. |
| `agent/kb_data.json` | The local knowledge base `kb_lookup` searches (GPU/model/vLLM/metric facts). |
| `agent/loop.py` | `Agent` — the multi-step tool-calling loop — and `SessionResult`. |
| `agent/logging_util.py` | Per-session JSONL logger; this is where TAT timestamps get captured. |
| `agent/mock_server.py` | An in-process stub of vLLM's `/v1/chat/completions` for offline testing/demo. **Not** a substitute for live-endpoint verification — see below. |
| `agent/cli.py` | `python -m agent.cli` entrypoint (`--task`, `--tasks-file`, `--demo`). |
| `tests/` | `unittest`-based tests; the tool tests hit no network, the loop tests run against `mock_server.py`. |
| `config/agent.config.example.json` | Template config file. |
| `requirements.txt` / `pyproject.toml` | Single runtime dependency: the `openai` Python SDK, pointed at vLLM's base URL — used purely as an OpenAI-compatible HTTP client, nothing talks to OpenAI's own API. |

## Config interface — base_url / model / reasoning-effort are never hardcoded

Precedence (**highest wins**): **CLI flag > environment variable > config file > built-in
default**. All three override mechanisms (env / CLI / config file) are supported, per the brief.

| Field | Env var | CLI flag | Default |
|---|---|---|---|
| `base_url` | `AGENT_BASE_URL` | `--base-url` | `http://localhost:8000/v1` |
| `api_key` | `AGENT_API_KEY` | `--api-key` | `EMPTY` (vLLM ignores it; the SDK just needs a non-empty string) |
| `model` | `AGENT_MODEL` | `--model` | `Qwen/Qwen3.6-27B-FP8` |
| `reasoning_effort` | `AGENT_REASONING_EFFORT` | `--reasoning-effort` | `medium` (one of `off`/`low`/`medium`/`high`) |
| `temperature` | `AGENT_TEMPERATURE` | `--temperature` | `0.7` |
| `top_p` | `AGENT_TOP_P` | `--top-p` | `0.9` |
| `max_tokens` | `AGENT_MAX_TOKENS` | `--max-tokens` | `1024` |
| `max_tool_turns` | `AGENT_MAX_TOOL_TURNS` | `--max-tool-turns` | `6` (safety cap on the tool loop) |
| `request_timeout` | `AGENT_REQUEST_TIMEOUT` | `--request-timeout` | `120.0` seconds |
| `log_dir` | `AGENT_LOG_DIR` | `--log-dir` | `./logs` |
| `system_prompt` | `AGENT_SYSTEM_PROMPT` | `--system-prompt` | see `config.py:DEFAULT_SYSTEM_PROMPT` |
| `extra_body` | `AGENT_EXTRA_BODY` (JSON string) | `--extra-body` (JSON string) | `{}` — escape hatch merged into every request |
| *(config file itself)* | `AGENT_CONFIG_FILE` | `--config PATH` | none |

Config file (`--config`/`AGENT_CONFIG_FILE`) is a JSON object with any subset of the field names
above — see `config/agent.config.example.json`. Unknown keys are rejected loudly (fail fast on a
typo rather than silently ignoring it).

Programmatic use (e.g. from `loadgen-builder`'s harness) doesn't need the CLI at all:

```python
from agent import AgentConfig, Agent

config = AgentConfig.from_env()              # defaults < config file < env vars
config.base_url = "http://<ec2-host>:8000/v1"  # or just override fields directly
agent = Agent(config)                          # safe to reuse across threads for concurrent sessions
result = agent.run_session("What is 12 * 7? Also look up the L40S VRAM capacity.")
print(result.status, result.tat_seconds, result.final_text)
```

`Agent` instances are safe to share across threads: `run_session()` only touches its own local
message history and its own `SessionLogger` (one file per session, keyed by `session_id`); the
underlying `openai.OpenAI` client is safe for concurrent use. This is the intended integration
point for the agent-driven load harness driving N concurrent sessions.

## Tools

Both are pure functions (`arguments: dict -> JSON-serializable dict`) that never raise — bad
arguments become `{"error": ...}` fed back to the model as the tool result, same as a real tool
reporting failure, so one bad model-generated tool call can't crash a session.

- **`calculator(expression: str)`** — safe arithmetic evaluation via a restricted `ast` walk (not
  `eval()`): numeric literals, `+ - * / // % **`, unary `+/-`, parentheses, the constants `pi`/`e`,
  and a small function whitelist (`sqrt abs round min max floor ceil log log10 sin cos tan pow`).
  Arbitrary code execution (e.g. `__import__(...)`, attribute access) is rejected at the AST level
  — see `tests/test_tools.py::TestCalculator.test_rejects_arbitrary_code_execution`.
- **`kb_lookup(query: str, top_k: int = 3)`** — keyword search over `agent/kb_data.json`, a small
  local knowledge base of facts *about this lab itself* (L40S/g6e.2xlarge specs, Qwen3.6-27B
  architecture/checkpoint facts, vLLM tool-calling/reasoning flags, and TTFT/ITL/TAT/KV-cache-hit-
  rate definitions). This is a real, checkable retrieval tool, not a stub — the "no such GPU"
  example in `--demo` below is answered from actual lab facts, not made up.

## Reasoning effort / thinking mode

`reasoning_effort` (`off`/`low`/`medium`/`high`) is translated into request-level fields in
`config.py:reasoning_extra_body()`:

- `off` -> `{"chat_template_kwargs": {"enable_thinking": false}}`
- `low`/`medium`/`high` -> `{"chat_template_kwargs": {"enable_thinking": true}, "reasoning_effort": "<level>"}`

**Confirmed** (per `containers/vllm/README.md`, the current Qwen3.6 model card, and the vLLM
`--reasoning-parser qwen3` docs): thinking mode on/off is a real per-request knob via
`chat_template_kwargs.enable_thinking`, and vLLM surfaces the model's thinking output as a
separate `message.reasoning_content` field (the agent logs its length per turn; see
`SessionLogger`).

**Not independently confirmed against a live endpoint**: whether Qwen3.6-27B / vLLM's
`qwen3` reasoning parser honors a *graded* `reasoning_effort` value the way some reasoning-model
APIs do, versus just ignoring it, versus erroring on an unrecognized field. To handle all three
outcomes without needing to know in advance: `Agent._call_model()` sends `reasoning_effort`
alongside `enable_thinking`, and if the server responds `400` specifically complaining about that
field, it retries once with just `enable_thinking` and proceeds — see
`tests/test_loop_mock.py::TestReasoningEffortFallback` for this exact path exercised against a
mock server configured to reject the field. If the real endpoint simply ignores unknown
`extra_body` keys (the more common vLLM behavior), no fallback is even needed and `off`/on-with-
effort-level both work as-is; if it errors, the fallback degrades gracefully to plain thinking
on/off, which is the confirmed knob.

## Turnaround Time (TAT) logging

Every call to `Agent.run_session()` writes `{log_dir}/session_<session_id>.jsonl`, one JSON object
per line:

- `session_start` — logged *before* the first model call, with `submitted_at` /
  `submitted_at_epoch` (client-side wall clock at the moment the task is submitted).
- `model_call` (one per turn) — `request_sent_at`/`response_received_at`/`latency_seconds`, token
  usage, `reasoning_chars`, which tools (if any) were requested, `finish_reason`.
- `tool_call` (one per executed tool call) — tool name, arguments, result, latency.
- `session_end` — logged *after* the final answer (or the `max_tool_turns` cutoff) is fully in
  hand, with `completed_at`/`completed_at_epoch` and `tat_seconds = completed_at - submitted_at`.
  This is the client-side TAT measurement SPEC.md calls for: it spans the whole round trip
  including every reasoning/tool step in between, not just one raw generation call.

`session_start` and `session_end` events are also appended to a shared
`{log_dir}/sessions_index.jsonl` so a load harness running many concurrent sessions can compute
TAT distribution across all of them without opening every per-session file.

`SessionResult` (the Python return value of `run_session()`) carries the same summary fields
(`tat_seconds`, `num_model_calls`, `num_tool_calls`, token counts, `status`, `final_text`,
`log_path`) for callers that don't want to re-parse the log.

## Install

```bash
cd agent
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Run — offline demo (bundled mock server, no live endpoint needed)

```bash
.venv/bin/python -m agent.cli --demo --print-transcript
```

This starts `agent/mock_server.py` in-process, points the agent at it, and runs the full
multi-step loop (calculator -> kb_lookup -> final answer) end to end. **This proves the loop's
mechanics work — it does not prove a real Qwen3.6-27B model actually chooses to call these tools
correctly.** See "Verification" below.

## Run — against the real vLLM endpoint

Exact command to run once the live `g6e.2xlarge` instance is reachable (e.g. via an SSM
port-forward to `localhost:8000`, or directly if on the same network):

```bash
.venv/bin/python -m agent.cli \
  --base-url http://localhost:8000/v1 \
  --model Qwen/Qwen3.6-27B-FP8 \
  --reasoning-effort high \
  --task "What is 15% of 640? Also, how much VRAM does the L40S GPU have, and does vLLM need any special flags to get tool calls working for it?" \
  --print-transcript
```

Or equivalently via env vars (handy for the load harness):

```bash
export AGENT_BASE_URL=http://localhost:8000/v1
export AGENT_MODEL=Qwen/Qwen3.6-27B-FP8
export AGENT_REASONING_EFFORT=high
.venv/bin/python -m agent.cli --task "..."
```

Batch mode (one session per line, e.g. for a quick multi-session smoke test):

```bash
.venv/bin/python -m agent.cli --tasks-file some_tasks.txt --log-dir ./logs/smoke-run
```

**Dependency on the serving side**: the agent only ever *receives* `tool_calls` if vLLM was
launched with `--enable-auto-tool-choice --tool-call-parser qwen3_coder` (done by default in
`containers/vllm/entrypoint.sh`). If a real run never triggers a tool call and the model instead
just answers in plain text on turn 0, check that flag on the server before assuming this loop is
broken — see `containers/vllm/README.md`'s own verification notes on this exact flag pair.

## Run the tests

```bash
cd agent
.venv/bin/python -m unittest discover -s tests -t . -v
```

39 tests, all passing locally as of this build; no network access required (the loop tests use
`mock_server.py`, not the real endpoint).

## Verification: what was actually run, vs. what needs the live endpoint

This agent was built on a dev box with no route to the real vLLM endpoint (it's VPC-scoped on the
`g6e.2xlarge` host; see the task brief). Everything below reflects that constraint honestly.

**Verified locally (this repo, this session, genuinely executed):**
- All 39 unit/integration tests pass (`tests/test_tools.py`, `tests/test_config.py`,
  `tests/test_loop_mock.py`), including:
  - Calculator safety (rejects arbitrary code execution / attribute access, handles div-by-zero
    and malformed expressions without raising).
  - `kb_lookup` returns real matches for real queries and a clean "no match" result otherwise.
  - **A full multi-step tool-calling session against the mock server**: model call -> `calculator`
    tool call -> tool result fed back -> model call -> `kb_lookup` tool call -> tool result fed
    back -> model call -> plain-text final answer citing both tool results. Confirms 3 model
    calls, 2 tool calls, growing message/context size per turn, and a non-zero `tat_seconds`.
  - The per-session JSONL log contains `session_start`/`session_end` with client-side
    `submitted_at_epoch`/`completed_at_epoch`, and `completed_at_epoch - submitted_at_epoch`
    matches the `SessionResult.tat_seconds` returned to the caller.
  - `reasoning_effort="off"` sends `enable_thinking: false` and produces no `reasoning_content`;
    non-off levels send `enable_thinking: true` plus a `reasoning_effort` field.
  - The `reasoning_effort`-rejected-by-server fallback path (mock server returns 400 specifically
    for that field; the agent retries once without it and the session still completes normally).
  - The `max_tool_turns` safety cap correctly halts the loop and marks
    `status="max_turns_exceeded"` instead of looping forever.
  - Config precedence (file < env < CLI), unknown-config-file-key rejection, and type coercion of
    env-var strings into floats/ints/JSON.
- `python -m agent.cli --demo --print-transcript` run manually end-to-end (not just under
  `unittest`) — output and the resulting `session_*.jsonl` log file were inspected directly (see
  transcript/log excerpt captured during this build).
- Pointing the CLI at a genuinely unreachable `base_url` (`http://127.0.0.1:1/v1`) confirmed the
  agent fails a single session cleanly (`status="error"`, logged, exit code 1) rather than
  crashing — relevant for a load harness running many sessions where one bad connection shouldn't
  take down the batch.
- Confirmed against the installed `openai` Python SDK (v2.46.0) directly, by inspection/small
  scripts during this build, not assumed from memory: `extra_body` is merged at the top level of
  the outgoing JSON request body (not nested under an `extra_body` key); response messages with
  server-added fields like `reasoning_content` are exposed as normal Python attributes (pydantic
  models with `extra="allow"`); `openai.BadRequestError`'s string representation includes the
  server's JSON error body (needed for the reasoning-effort-fallback detection to work); tool-call
  objects round-trip cleanly through `.model_dump()` into the exact dict shape the Chat Completions
  API expects for `assistant` messages with `tool_calls`.

**NOT verified — genuinely requires the live vLLM/Qwen3.6-27B endpoint, not attempted/faked here:**
- Whether the real Qwen3.6-27B model, given these two tool schemas and a real task, actually
  chooses to call `calculator`/`kb_lookup` correctly (the mock server scripts a fixed sequence
  regardless of input content — it tests loop mechanics, not model behavior).
- Whether `--tool-call-parser qwen3_coder` on the real server correctly parses the model's tool
  call output into OpenAI-format `message.tool_calls` in practice (the serving side's own README
  documents this flag as confirmed-valid-but-not-yet-run-end-to-end at the model-load stage; this
  agent has no way to confirm the *other* half of that round trip without the live server).
  Note: the model's tool-call *format* is normally described by vLLM's docs as XML-ish
  (`<tool_call>...`) that the `qwen3_coder` parser converts into OpenAI JSON — if the real run
  shows tool_calls arriving malformed or not at all, check the serving-side parser flag first, per
  the brief.
- Whether the real server accepts, ignores, or 400s the `reasoning_effort` extra field (the
  fallback path is unit-tested against a mock that simulates rejection, but its real-world trigger
  condition is unconfirmed).
- Real TTFT/ITL/TPS numbers under load — those are `genai-perf` / vLLM `/metrics` territory per
  SPEC.md, not this agent's job; this agent only measures client-side TAT.
- Real multi-turn behavior at scale / under concurrency (that's `loadgen-builder`'s harness driving
  many `Agent` sessions — this repo just confirms the single-session loop and thread-safety
  contract it depends on).

**Recommendation**: invoke the `checker` subagent for the static/mock-verified portions above.
The genuinely end-to-end "multi-step tool task completes against the real vLLM endpoint" proof
needs the live `g6e.2xlarge` instance and should happen there (per the task brief, driven via SSM)
— `checker`'s own instructions already anticipate this ("if you can't verify a condition without
[access you don't have], say so explicitly").

"""Command-line entrypoint.

    python -m agent.cli --task "What is 12 * 7? Also look up the L40S VRAM capacity." \\
        --base-url http://<vllm-host>:8000/v1 --model Qwen/Qwen3.6-27B-FP8 \\
        --reasoning-effort high

    python -m agent.cli --demo
        # Runs the same loop against the bundled in-process mock server (agent/mock_server.py)
        # instead of a real endpoint -- a self-contained way to see the whole tool-calling loop
        # execute without a live vLLM instance. NOT a substitute for live-endpoint verification;
        # see README.md.

See config.py for the full env/CLI/config-file precedence chain.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

from .config import add_config_arguments, config_from_namespace
from .loop import Agent, SessionResult

DEMO_TASK = "What is 12 * 7? Also, how much VRAM does the L40S GPU have?"


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m agent.cli",
        description="Run the tool-calling agent for one or more tasks against an "
        "OpenAI-compatible endpoint (default: a vLLM-served Qwen3.6-27B).",
    )
    add_config_arguments(parser)
    parser.add_argument("--task", default=None, help="A single task/prompt to run.")
    parser.add_argument(
        "--tasks-file",
        default=None,
        help="Path to a text file with one task per line; runs one session per line.",
    )
    parser.add_argument(
        "--session-id",
        default=None,
        help="Explicit session id (only meaningful with --task; auto-generated otherwise).",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run against the bundled in-process mock server instead of a real endpoint.",
    )
    parser.add_argument(
        "--print-transcript",
        action="store_true",
        help="Print the full per-turn transcript (including reasoning_content) for each session.",
    )
    return parser


def _collect_tasks(args: argparse.Namespace) -> list[str]:
    if args.task and args.tasks_file:
        raise SystemExit("--task and --tasks-file are mutually exclusive")
    if args.tasks_file:
        with open(args.tasks_file, "r", encoding="utf-8") as f:
            tasks = [line.strip() for line in f if line.strip()]
        if not tasks:
            raise SystemExit(f"--tasks-file {args.tasks_file!r} contained no non-empty lines")
        return tasks
    return [args.task or DEMO_TASK]


def _print_result(result: SessionResult, print_transcript: bool) -> None:
    print(f"session_id:        {result.session_id}")
    print(f"status:            {result.status}" + (f" ({result.error})" if result.error else ""))
    print(f"tat_seconds:       {result.tat_seconds:.3f}")
    print(f"num_model_calls:   {result.num_model_calls}")
    print(f"num_tool_calls:    {result.num_tool_calls}")
    print(f"prompt_tokens:     {result.total_prompt_tokens}")
    print(f"completion_tokens: {result.total_completion_tokens}")
    print(f"log_path:          {result.log_path}")
    print(f"final answer:      {result.final_text}")
    if print_transcript:
        print("--- transcript ---")
        print(json.dumps(result.transcript, indent=2))
    print()


def main(argv: Optional[list] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.demo:
        # Local import to avoid pulling http.server machinery into normal (non-demo) runs.
        from .mock_server import MockVLLMServer

        with MockVLLMServer() as srv:
            if args.base_url is None:
                args.base_url = srv.base_url
            if args.model is None:
                args.model = srv.model_id
            config = config_from_namespace(args)
            print(f"[demo] mock server listening at {srv.base_url}")
            agent = Agent(config)
            for task in _collect_tasks(args):
                result = agent.run_session(task, session_id=args.session_id)
                _print_result(result, args.print_transcript)
        return 0

    config = config_from_namespace(args)
    agent = Agent(config)
    exit_code = 0
    for task in _collect_tasks(args):
        result = agent.run_session(task, session_id=args.session_id)
        _print_result(result, args.print_transcript)
        if result.status != "ok":
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main())

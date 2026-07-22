"""
SELF-TEST SCAFFOLDING ONLY -- not the real agent/.

Minimal `python -m agent.cli` stand-in matching the assumed interface's printed-output contract
(see loadgen/agent_harness/README.md "Assumed agent/ interface"), so run_harness.py's CliEngine
has a real subprocess to invoke and real stdout to parse end to end.
"""
from __future__ import annotations

import argparse
import sys

from .config import AgentConfig
from .loop import Agent


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--task", required=True)
    p.add_argument("--base-url", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--session-id", default=None)
    p.add_argument("--reasoning-effort", default=None)
    p.add_argument("--max-tokens", type=int, default=None)
    args = p.parse_args(argv)

    config = AgentConfig(base_url=args.base_url, model=args.model)
    if args.reasoning_effort:
        config.reasoning_effort = args.reasoning_effort
    if args.max_tokens:
        config.max_tokens = args.max_tokens

    agent = Agent(config)
    result = agent.run_session(args.task, session_id=args.session_id)

    print(f"session_id:        {result.session_id}")
    print(f"status:            {result.status}" + (f" ({result.error})" if result.error else ""))
    print(f"tat_seconds:       {result.tat_seconds:.3f}")
    print(f"num_model_calls:   {result.num_model_calls}")
    print(f"num_tool_calls:    {result.num_tool_calls}")
    print(f"prompt_tokens:     {result.total_prompt_tokens}")
    print(f"completion_tokens: {result.total_completion_tokens}")
    print(f"log_path:          {result.log_path}")
    print(f"final answer:      {result.final_text}")
    print()
    return 0 if result.status == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())

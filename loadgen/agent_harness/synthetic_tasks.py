"""
loadgen/agent_harness/synthetic_tasks.py

Generates synthetic agent tasks (prompts) with a configurable average input length and a
best-effort output-length hint, per SPEC.md's "average input and output length" knob. Mirrors
genai-perf's own --synthetic-input-tokens-mean/--output-tokens-mean knobs (mean + stddev,
Gaussian, floor-clamped) so the two load-generation paths are knob-comparable.

Two caveats, documented rather than hidden:
- Length here is measured in whitespace-separated words, not model tokens -- the harness has no
  guaranteed access to the model's tokenizer (unlike genai-perf, which loads one explicitly via
  --tokenizer). Words are a reasonable proxy for relative sizing across runs; don't treat the
  configured mean as an exact token count. Document this in any comparison against genai-perf's
  token-exact ISL.
- Output length is a *hint* baked into the task text ("Answer in about N words.") plus (if the
  adapter's engine supports it) a `max_tokens` passthrough -- the agent, not this generator, has
  the actual final say on how long its answer is. This is what SPEC.md means by "average output
  length (observed per run, alongside the configured knob value)": configure a target, then
  measure what actually came out (num_model_calls / total_completion_tokens from the session
  result already give you that on the agent side).
"""
from __future__ import annotations

import random
from dataclasses import dataclass

# Content mixed into filler text and designed to plausibly trigger the two tool categories
# SPEC.md requires ("at minimum a calculator tool and a retrieval/lookup tool") -- generic enough
# that it doesn't assume any particular tool *name* from the agent/ implementation, only that
# *some* calculator-shaped and *some* lookup-shaped question exists in the mix.
_ARITHMETIC_HOOKS = [
    "What is {a} * {b}?",
    "Compute ({a} + {b}) * {a} for me precisely.",
    "What's {a} percent of {b}?",
    "If I have {a} groups of {b} items, how many items total?",
]
_LOOKUP_HOOKS = [
    "How much VRAM does an NVIDIA L40S GPU have?",
    "What precision is Qwen3.6-27B served at in this lab, and why?",
    "What does TTFT stand for in LLM serving benchmarks?",
    "What KV-cache management strategies does vLLM support?",
]
_FILLER_SENTENCES = [
    "I'm evaluating this GPU serving setup as part of a sizing exercise.",
    "Please be concise but show your reasoning briefly.",
    "This is a synthetic load-generation request, not a real user question.",
    "Treat this as one turn of a longer conversation about GPU inference sizing.",
    "Context: we're measuring turnaround time under concurrent agent sessions.",
    "Feel free to use any tools available to you to get an exact answer.",
    "We care about both correctness and how long your response takes to produce.",
    "This prompt is padded with filler text to hit a target average input length.",
]


@dataclass(frozen=True)
class SyntheticTaskConfig:
    isl_mean: float = 40.0     # target average input length, in words
    isl_stddev: float = 10.0
    osl_hint_mean: float = 150.0  # target average output length hint, in words
    osl_hint_stddev: float = 50.0
    tool_mix: float = 0.7      # fraction of tasks that include a tool-triggering hook
    seed: "int | None" = None


def _sample_len(mean: float, stddev: float, rng: random.Random, floor: int = 1) -> int:
    return max(floor, round(rng.gauss(mean, stddev))) if stddev > 0 else max(floor, round(mean))


def generate_tasks(n: int, config: SyntheticTaskConfig) -> list[str]:
    """Generate `n` synthetic task strings per `config`. Deterministic if config.seed is set."""
    rng = random.Random(config.seed)
    tasks = []
    for _ in range(n):
        target_words = _sample_len(config.isl_mean, config.isl_stddev, rng)
        osl_hint = _sample_len(config.osl_hint_mean, config.osl_hint_stddev, rng, floor=10)

        parts: list[str] = []
        if rng.random() < config.tool_mix:
            hook_pool = _ARITHMETIC_HOOKS if rng.random() < 0.5 else _LOOKUP_HOOKS
            hook = rng.choice(hook_pool)
            parts.append(hook.format(a=rng.randint(2, 97), b=rng.randint(2, 97)))

        while sum(len(p.split()) for p in parts) < target_words:
            parts.append(rng.choice(_FILLER_SENTENCES))

        parts.append(f"Answer in about {osl_hint} words.")
        tasks.append(" ".join(parts))
    return tasks

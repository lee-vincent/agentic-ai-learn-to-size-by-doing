"""
experiment_cli/knobs.py

The generic knob abstraction (nice-to-have per the build brief): a KnobSpec is just "how does one
raw sweep-config value turn into updates to the run's base configuration dict" plus whether it
needs a vLLM server restart (in which case this CLI version refuses to run it -- see cli.py).

`base` (the per-run configuration dict threaded through cli.py's run_one()) always carries these
six keys -- one per SPEC.md knob dimension -- regardless of which single knob is being swept this
run: `precision`, `kv_cache_strategy`, `decoding_algorithm`, `concurrency`, `isl_mean`, `osl_mean`,
`reasoning_effort`. A knob's `apply(value, base)` returns just the subset of that dict it changes;
cli.py does `base.update(knob.apply(value, base))` once per run, then reads `concurrency`/
`isl_mean`/`osl_mean`/`reasoning_effort` straight out of `base` to build both the genai-perf and
harness invocations, and copies all six into the results row verbatim (SPEC.md: "tagged with the
full knob configuration used"). This is what makes the abstraction generic: adding a new knob is
one `_apply_xxx()` function + one registry entry -- no other file needs to change.

Implemented (no server restart needed):
  - concurrency    -- MUST work per the build brief; this is the one actually exercised in the
                       shipped dry-run and the recommended real sweep.
  - isl_osl        -- bonus: average input/output length, format "isl:osl" (tokens), e.g. "200:200"
  - reasoning_effort -- bonus: Qwen thinking-mode effort, one of off/low/medium/high. NOTE this
                       only reaches the agent harness -- genai-perf's raw completions endpoint
                       (run_sweep.sh's ENDPOINT_TYPE=completions default) bypasses the chat
                       template entirely, so it never invokes the reasoning parser either way; see
                       README.md "Extension points" for the caveat this implies for that knob's
                       genai-perf-sourced metrics.

Extension points only (NOT implemented -- selecting one of these is a clear, immediate error, not
a silent no-op or a wrong number):
  - precision          -- needs a vLLM server restart with a different --quantization/checkpoint.
  - kv_cache_strategy   -- needs a vLLM server restart with different --enable-prefix-caching /
                           --kv-cache-dtype / chunked-prefill flags.
  - decoding_algorithm  -- speculative decoding needs a server restart (--speculative-config);
                           greedy vs. parallel-sampling vs. beam-search are per-request knobs in
                           principle but are not wired into this CLI version either.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional


class KnobError(Exception):
    pass


@dataclass
class KnobSpec:
    name: str
    restart_required: bool
    description: str
    # None for restart_required knobs (there's nothing to "apply" -- see cli.py's refusal path).
    apply: Optional[Callable[[Any, dict], dict]] = None


def _parse_pair(value: Any) -> tuple[int, int]:
    s = str(value)
    if ":" not in s:
        raise KnobError(f"isl_osl knob value {value!r} must be 'isl:osl', e.g. '200:200'")
    isl_s, _, osl_s = s.partition(":")
    try:
        return int(float(isl_s)), int(float(osl_s))
    except ValueError as exc:
        raise KnobError(f"isl_osl knob value {value!r} must be 'isl:osl' with numeric parts") from exc


def _apply_concurrency(value: Any, base: dict) -> dict:
    try:
        n = int(value)
    except (TypeError, ValueError) as exc:
        raise KnobError(f"concurrency knob value {value!r} must be an integer") from exc
    if n < 1:
        raise KnobError(f"concurrency knob value {value!r} must be >= 1")
    return {"concurrency": n}


def _apply_isl_osl(value: Any, base: dict) -> dict:
    isl, osl = _parse_pair(value)
    return {"isl_mean": isl, "osl_mean": osl}


_VALID_REASONING = ("off", "low", "medium", "high")


def _apply_reasoning_effort(value: Any, base: dict) -> dict:
    v = str(value).strip().lower()
    if v not in _VALID_REASONING:
        raise KnobError(f"reasoning_effort knob value {value!r} must be one of {_VALID_REASONING}")
    return {"reasoning_effort": v}


KNOBS: dict[str, KnobSpec] = {
    "concurrency": KnobSpec(
        name="concurrency",
        restart_required=False,
        description="Number of concurrent users/sessions (SPEC.md knob). No server restart "
                     "needed -- this is the knob the Phase 5 goal requires at minimum.",
        apply=_apply_concurrency,
    ),
    "isl_osl": KnobSpec(
        name="isl_osl",
        restart_required=False,
        description="Average input/output length pair, format 'isl:osl' in tokens (SPEC.md "
                     "knob). No server restart needed.",
        apply=_apply_isl_osl,
    ),
    "reasoning_effort": KnobSpec(
        name="reasoning_effort",
        restart_required=False,
        description="Qwen thinking-mode effort level (SPEC.md knob), one of off/low/medium/high. "
                     "Passed through to the agent harness only -- see module docstring caveat "
                     "about genai-perf's completions endpoint bypassing the reasoning parser.",
        apply=_apply_reasoning_effort,
    ),
    "precision": KnobSpec(
        name="precision",
        restart_required=True,
        description="Parameter precision (FP8 vs INT4/AWQ, SPEC.md knob) requires restarting the "
                     "vLLM container with a different checkpoint/--quantization flag. Extension "
                     "point only in this CLI version -- see README.md 'Extension points'.",
        apply=None,
    ),
    "kv_cache_strategy": KnobSpec(
        name="kv_cache_strategy",
        restart_required=True,
        description="KV-cache management strategy (PagedAttention/prefix caching/chunked "
                     "prefill/KV-cache quantization, SPEC.md knob) requires restarting the vLLM "
                     "container with different flags. Extension point only -- see README.md "
                     "'Extension points'.",
        apply=None,
    ),
    "decoding_algorithm": KnobSpec(
        name="decoding_algorithm",
        restart_required=True,
        description="Decoding algorithm (greedy/parallel sampling/speculative decoding/beam "
                     "search, SPEC.md knob) -- speculative decoding requires a vLLM server "
                     "restart (--speculative-config); this CLI version does not implement any "
                     "variant of this knob. Extension point only -- see README.md 'Extension "
                     "points'.",
        apply=None,
    ),
}


def get_knob(name: str) -> KnobSpec:
    try:
        return KNOBS[name]
    except KeyError:
        raise KnobError(
            f"unknown knob {name!r}; known knobs: {sorted(KNOBS)} (precision/kv_cache_strategy/"
            "decoding_algorithm are documented extension points, not runnable -- see README.md)"
        ) from None

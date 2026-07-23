"""
experiment_cli/config.py

Loads a sweep config file (JSON, fully supported; a minimal YAML-lite also supported -- see
`_load_yaml_lite()` docstring for exactly what subset of YAML it accepts) and fills in defaults.
Tool paths and base URLs are never hardcoded here beyond a localhost default -- every one of them
is overridable by the config file, an environment variable, or (highest precedence) a CLI flag;
see cli.py's `_apply_cli_overrides()`.

Required config keys: `knob` (a name from experiment_cli.knobs.KNOBS) and `values` (a list with
at least one value -- the Phase 5 goal wants at least two).

See experiment-cli/sweeps/*.json for real examples and README.md "Sweep config format" for the
full schema table.
"""
from __future__ import annotations

import json
import os
from typing import Any

# The six SPEC.md knob dimensions every run tags, whether or not they're the one being swept this
# invocation. Defaults reflect vLLM's own out-of-the-box behavior on this stack (see
# containers/vllm/README.md and monitoring/README.md): FP8 checkpoint, PagedAttention + automatic
# prefix caching both on by default, greedy decoding (temperature 0 requests from genai-perf),
# thinking mode off. These are NOT verified against a live server by this module -- they're just
# what gets written into the results row's tag columns unless a sweep config's `fixed:` block (or
# the active knob) overrides them.
DEFAULT_FIXED: dict[str, Any] = {
    "precision": "fp8",
    "kv_cache_strategy": "paged_attention+prefix_caching",
    "decoding_algorithm": "greedy",
    "concurrency": 4,
    "isl_mean": 200,
    "osl_mean": 200,
    "reasoning_effort": "off",
}


class ConfigError(Exception):
    pass


def _parse_scalar(value: str) -> Any:
    """Try JSON first (handles ints/floats/bools/null, and inline lists of JSON-legal scalars
    like `[1, 8]`); fall back to a bare (optionally quoted) string for YAML-ish bareword scalars
    JSON rejects, e.g. `off`. A `[...]` value that ISN'T valid JSON (e.g. `[off, high]` -- bare,
    unquoted words) is instead split on top-level commas and each item re-parsed as a scalar --
    this is a flat-list-only fallback (no nested lists/commas-in-strings), sufficient for this
    CLI's sweep configs; write JSON if you need more."""
    value = value.strip()
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        pass
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(item) for item in inner.split(",")]
    return value.strip('"').strip("'")


def _load_yaml_lite(path: str) -> dict:
    """A deliberately minimal YAML subset -- NOT a general YAML parser. Supports: `#` comments,
    blank lines, flat `key: value` pairs, one level of nested mapping via a trailing-colon key
    (used for the `fixed:` block), and JSON-syntax inline scalars/lists as the value (so
    `values: [1, 8]` and `reasoning_effort: "off"` both work). If your config needs more than
    this, write JSON instead -- it's the fully-supported, recommended format (see README.md)."""
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    root: dict = {}
    stack: list[tuple[int, dict]] = [(-1, root)]
    for lineno, raw_line in enumerate(lines, start=1):
        line = raw_line.rstrip("\n")
        stripped = line.split("#", 1)[0].rstrip()
        if not stripped.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if ":" not in stripped:
            raise ConfigError(f"{path}:{lineno}: cannot parse yaml-lite line: {raw_line!r}")
        key, _, value = stripped.strip().partition(":")
        key = key.strip()
        value = value.strip()
        if value == "":
            new_map: dict = {}
            parent[key] = new_map
            stack.append((indent, new_map))
        else:
            parent[key] = _parse_scalar(value)
    return root


def load_sweep_config(path: str) -> dict:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".yaml", ".yml"):
        raw = _load_yaml_lite(path)
    else:
        with open(path, "r", encoding="utf-8") as f:
            try:
                raw = json.load(f)
            except json.JSONDecodeError as exc:
                raise ConfigError(f"{path}: not valid JSON ({exc})") from exc
    return with_defaults(raw, source=path)


def with_defaults(raw: dict, source: str = "<config>") -> dict:
    cfg = dict(raw)
    if "knob" not in cfg:
        raise ConfigError(f"{source}: sweep config must set 'knob' (e.g. \"concurrency\")")
    if "values" not in cfg or not isinstance(cfg["values"], list) or not cfg["values"]:
        raise ConfigError(f"{source}: sweep config must set 'values' to a non-empty list")

    cfg.setdefault("model", os.environ.get("EXPERIMENT_CLI_MODEL", "Qwen/Qwen3.6-27B-FP8"))
    cfg.setdefault("served_model_name", cfg["model"])
    cfg.setdefault("genai_perf_base_url",
                    os.environ.get("GENAI_PERF_BASE_URL", "http://localhost:8000"))
    cfg.setdefault("harness_base_url",
                    os.environ.get("HARNESS_BASE_URL", "http://localhost:8000/v1"))
    cfg.setdefault("prometheus_url", os.environ.get("PROMETHEUS_URL", "http://localhost:9090"))
    cfg.setdefault("prometheus_step", 5)
    cfg.setdefault("requests_per_concurrency", 10)
    cfg.setdefault("warmup_requests", 2)
    cfg.setdefault("harness_concurrency_cap", 16)
    cfg.setdefault("endpoint_type", "completions")
    cfg.setdefault("ignore_eos", True)
    cfg.setdefault("tokenizer", None)
    cfg.setdefault("isl_stddev_frac", 0.1)  # ISL_STDDEV = isl_mean * this, unless overridden
    cfg.setdefault("osl_stddev_frac", 0.1)

    fixed = dict(DEFAULT_FIXED)
    fixed.update(cfg.get("fixed") or {})
    cfg["fixed"] = fixed
    return cfg

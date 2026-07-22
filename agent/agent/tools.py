"""Tool implementations + OpenAI-format schemas for the agent's tool-calling loop.

Two tools, per SPEC.md's minimum:
  - `calculator`: safe arithmetic evaluation (no arbitrary code execution — see _safe_eval)
  - `kb_lookup`:  keyword search over a small local knowledge base (an offline retrieval/lookup
                  tool; the KB itself documents this lab's own GPU/model/metric facts, so it
                  doubles as a real, checkable retrieval target rather than a fake stub).

Both tools are pure functions: (arguments: dict) -> JSON-serializable dict. They never raise —
malformed arguments produce {"error": "..."} instead, which gets fed back to the model as the
tool result exactly like a real tool reporting a failure would, so one bad tool call from the
model can't crash the session.
"""
from __future__ import annotations

import ast
import json
import math
import operator
import pathlib
from typing import Any, Callable

_KB_PATH = pathlib.Path(__file__).parent / "kb_data.json"
_kb_cache: list[dict] | None = None


# ---------------------------------------------------------------------------
# calculator
# ---------------------------------------------------------------------------

_ALLOWED_BINOPS: dict[type, Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_ALLOWED_UNARY: dict[type, Callable[[Any], Any]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}
_ALLOWED_FUNCS: dict[str, Callable[..., Any]] = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sqrt": math.sqrt,
    "floor": math.floor,
    "ceil": math.ceil,
    "log": math.log,
    "log10": math.log10,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "pow": pow,
}
_ALLOWED_NAMES = {"pi": math.pi, "e": math.e}


class CalculatorError(ValueError):
    pass


def _safe_eval(node: ast.AST) -> Any:
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise CalculatorError(f"unsupported constant: {node.value!r}")
        return node.value
    if isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in _ALLOWED_BINOPS:
            raise CalculatorError(f"operator not allowed: {op_type.__name__}")
        return _ALLOWED_BINOPS[op_type](_safe_eval(node.left), _safe_eval(node.right))
    if isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        if op_type not in _ALLOWED_UNARY:
            raise CalculatorError(f"unary operator not allowed: {op_type.__name__}")
        return _ALLOWED_UNARY[op_type](_safe_eval(node.operand))
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _ALLOWED_FUNCS:
            raise CalculatorError("function not allowed (only a fixed whitelist is permitted)")
        if node.keywords:
            raise CalculatorError("keyword arguments are not allowed")
        args = [_safe_eval(a) for a in node.args]
        return _ALLOWED_FUNCS[node.func.id](*args)
    if isinstance(node, ast.Name):
        if node.id in _ALLOWED_NAMES:
            return _ALLOWED_NAMES[node.id]
        raise CalculatorError(f"unknown identifier: {node.id}")
    raise CalculatorError(f"unsupported expression element: {type(node).__name__}")


def calculator(arguments: dict) -> dict:
    """Evaluate a basic arithmetic expression. Only numeric literals, +-*/, //, %, **, unary
    +/-, parentheses, the constants pi/e, and a small whitelist of math functions (sqrt, abs,
    round, min, max, floor, ceil, log, log10, sin, cos, tan, pow) are permitted — this is a
    restricted AST walk, not `eval()`, so it cannot execute arbitrary code."""
    expr = arguments.get("expression")
    if not isinstance(expr, str) or not expr.strip():
        return {"error": "missing or empty 'expression' argument"}
    try:
        parsed = ast.parse(expr, mode="eval")
        result = _safe_eval(parsed)
    except (CalculatorError, SyntaxError, ZeroDivisionError, TypeError, ValueError) as exc:
        return {"error": f"could not evaluate expression {expr!r}: {exc}"}
    return {"expression": expr, "result": result}


# ---------------------------------------------------------------------------
# kb_lookup
# ---------------------------------------------------------------------------


def _load_kb() -> list[dict]:
    global _kb_cache
    if _kb_cache is None:
        with open(_KB_PATH, "r", encoding="utf-8") as f:
            _kb_cache = json.load(f)
    return _kb_cache


def kb_lookup(arguments: dict) -> dict:
    """Keyword search over the lab's local knowledge base (GPU/instance specs, model/checkpoint
    facts, vLLM flags, metric definitions — see kb_data.json). Scores documents by counting how
    many query terms appear in their title/text/tags and returns the top_k matches."""
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        return {"error": "missing or empty 'query' argument"}
    top_k = arguments.get("top_k", 3)
    try:
        top_k = max(1, min(int(top_k), 10))
    except (TypeError, ValueError):
        top_k = 3

    docs = _load_kb()
    q_terms = {t for t in query.lower().split() if t}
    scored = []
    for doc in docs:
        haystack = (
            doc["title"] + " " + doc["text"] + " " + " ".join(doc.get("tags", []))
        ).lower()
        score = sum(haystack.count(term) for term in q_terms)
        if score > 0:
            scored.append((score, doc))
    scored.sort(key=lambda pair: pair[0], reverse=True)

    if not scored:
        return {"query": query, "results": [], "note": "no matching entries in local knowledge base"}
    return {
        "query": query,
        "results": [{"title": d["title"], "text": d["text"]} for _, d in scored[:top_k]],
    }


# ---------------------------------------------------------------------------
# OpenAI-format tool schemas + dispatch
# ---------------------------------------------------------------------------

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "calculator",
            "description": (
                "Evaluate a basic arithmetic expression exactly (numbers, + - * / // % **, "
                "parentheses, pi/e, and sqrt/abs/round/min/max/floor/ceil/log/log10/sin/cos/tan/"
                "pow). Use this instead of doing arithmetic yourself whenever precision matters."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "The arithmetic expression to evaluate, e.g. '12 * (7 + 1) / 2'.",
                    }
                },
                "required": ["expression"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "kb_lookup",
            "description": (
                "Search a small local knowledge base about this GPU-serving lab (GPU/instance "
                "specs, Qwen3.6-27B model/checkpoint facts, vLLM serving flags, and metric "
                "definitions like TTFT/ITL/TAT/KV-cache-hit-rate) by keyword query."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Keywords to search for, e.g. 'L40S VRAM' or 'TTFT definition'.",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Maximum number of matching entries to return (default 3).",
                        "default": 3,
                    },
                },
                "required": ["query"],
            },
        },
    },
]

TOOL_FUNCTIONS: dict[str, Callable[[dict], dict]] = {
    "calculator": calculator,
    "kb_lookup": kb_lookup,
}


def execute_tool_call(name: str, arguments_json: str | None) -> str:
    """Dispatch one OpenAI-format tool call (`function.name`, `function.arguments` as a raw JSON
    string) to the matching tool function and return a JSON string suitable for a `role: tool`
    message's `content`. Never raises — unknown tool names and malformed argument JSON both
    become {"error": ...} results instead of exceptions, matching how a real tool call would
    report failure back to the model rather than crashing the session."""
    fn = TOOL_FUNCTIONS.get(name)
    if fn is None:
        return json.dumps({"error": f"unknown tool: {name}"})
    try:
        args = json.loads(arguments_json) if arguments_json else {}
        if not isinstance(args, dict):
            raise ValueError("tool call arguments must decode to a JSON object")
    except (json.JSONDecodeError, ValueError) as exc:
        return json.dumps({"error": f"invalid tool arguments JSON: {exc}"})
    return json.dumps(fn(args))

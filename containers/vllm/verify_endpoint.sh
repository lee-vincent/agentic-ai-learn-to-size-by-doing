#!/usr/bin/env bash
# containers/vllm/verify_endpoint.sh
#
# Run this ON THE GPU HOST (g6e.2xlarge) after `docker compose up -d` /
# `systemctl start vllm` / a direct `docker run`, once the container has
# finished loading the model (watch `docker compose logs -f vllm` or
# `journalctl -u vllm -f` for "Application startup complete" / "Uvicorn
# running on http://0.0.0.0:8000").
#
# This is exactly what this repo could NOT run on the (GPU-less) dev box
# this container was built on -- see containers/vllm/README.md
# "Verification: local vs. GPU-host" for the split. Nothing here is faked;
# every check hits a real HTTP endpoint and prints the real response.
#
# Usage: ./verify_endpoint.sh [base_url]   (default base_url: http://localhost:8000)
set -uo pipefail

BASE_URL="${1:-http://localhost:8000}"
FAIL=0

pass() { echo "PASS: $1"; }
fail() { echo "FAIL: $1"; FAIL=1; }

echo "=== 1. /health ==="
code=$(curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/health")
if [ "$code" = "200" ]; then pass "/health returned 200"; else fail "/health returned $code"; fi

echo
echo "=== 2. /v1/models ==="
models_body=$(curl -s "$BASE_URL/v1/models")
echo "$models_body"
if echo "$models_body" | grep -q '"id"'; then
  pass "/v1/models returned a model list"
else
  fail "/v1/models did not return a recognizable model list"
fi

echo
echo "=== 3. /v1/chat/completions (non-streaming, thinking mode default-on) ==="
chat_body=$(curl -s -X POST "$BASE_URL/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d '{
        "model": "Qwen/Qwen3.6-27B-FP8",
        "messages": [{"role": "user", "content": "Reply with exactly the two words: hello world"}],
        "max_tokens": 64,
        "temperature": 0.7,
        "top_p": 0.8
      }')
echo "$chat_body" | python3 -m json.tool 2>/dev/null || echo "$chat_body"
if echo "$chat_body" | grep -q '"choices"' && echo "$chat_body" | grep -q '"content"'; then
  pass "/v1/chat/completions returned a well-formed completion"
else
  fail "/v1/chat/completions response missing choices/content -- see raw body above"
fi

echo
echo "=== 4. /v1/chat/completions with tool calling (validates --enable-auto-tool-choice --tool-call-parser qwen3_coder) ==="
tool_body=$(curl -s -X POST "$BASE_URL/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d '{
        "model": "Qwen/Qwen3.6-27B-FP8",
        "messages": [{"role": "user", "content": "What is 47 * 89? Use the calculator tool."}],
        "max_tokens": 512,
        "tools": [{
          "type": "function",
          "function": {
            "name": "calculator",
            "description": "Evaluate a basic arithmetic expression",
            "parameters": {
              "type": "object",
              "properties": {"expression": {"type": "string"}},
              "required": ["expression"]
            }
          }
        }],
        "tool_choice": "auto"
      }')
echo "$tool_body" | python3 -m json.tool 2>/dev/null || echo "$tool_body"
if echo "$tool_body" | grep -q '"tool_calls"'; then
  pass "/v1/chat/completions emitted an OpenAI-format tool_calls block"
else
  fail "no tool_calls in response -- check --enable-auto-tool-choice/--tool-call-parser wiring (this is the flag agent-builder depends on)"
fi

echo
echo "=== 5. /metrics (vLLM native Prometheus endpoint) ==="
metrics_body=$(curl -s "$BASE_URL/metrics")
if echo "$metrics_body" | grep -q '^vllm:'; then
  pass "/metrics exposes vllm: prefixed series"
  # These exact names were verified against vllm/v1/metrics/loggers.py for
  # the pinned vllm==0.25.1 -- see README.md "Verification" section.
  echo "$metrics_body" | grep -E '^vllm:(time_to_first_token_seconds|inter_token_latency_seconds|e2e_request_latency_seconds|num_requests_(running|waiting)|kv_cache_usage_perc|prefix_cache_(queries|hits))' | head -30
else
  fail "/metrics did not contain any vllm: series"
fi

echo
if [ "$FAIL" -eq 0 ]; then
  echo "=== ALL CHECKS PASSED ==="
else
  echo "=== ONE OR MORE CHECKS FAILED -- see FAIL lines above ==="
fi
exit "$FAIL"

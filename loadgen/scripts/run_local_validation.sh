#!/usr/bin/env bash
# loadgen/scripts/run_local_validation.sh
#
# Runs BOTH loadgen/ tools end to end against a local stub OpenAI-compatible server (NOT the real
# Qwen3.6-27B vLLM endpoint -- that lives on a VPC-scoped EC2 host this dev box cannot reach; see
# loadgen/README.md "Verified vs. deferred"). This is a mechanical smoke test: does genai-perf
# parse our config and produce well-formed output, does the harness correctly drive concurrent
# sessions and log TAT, end to end, against a real (if fake) HTTP server. It proves NOTHING about
# real Qwen3.6-27B performance numbers -- do not read the numbers this prints as benchmark results.
#
# Requires: genai-perf installed in a venv on PATH (see loadgen/genai-perf/README.md "Setup").
#
# Usage: ./run_local_validation.sh
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."   # repo root (loadgen/scripts/.. /..)

STUB_PORT="${STUB_PORT:-8098}"
STUB_URL="http://localhost:${STUB_PORT}"

echo "=== starting stub server on :$STUB_PORT ==="
python3 loadgen/scripts/stub_vllm_server.py --port "$STUB_PORT" > /tmp/loadgen-stub.log 2>&1 &
STUB_PID=$!
trap 'echo "=== stopping stub server (pid $STUB_PID) ==="; kill $STUB_PID 2>/dev/null || true' EXIT
sleep 1
curl -sf "$STUB_URL/health" >/dev/null || { echo "stub server did not come up -- see /tmp/loadgen-stub.log"; exit 1; }
echo "stub server healthy."
echo

if command -v genai-perf >/dev/null 2>&1; then
  echo "=== genai-perf sweep against the stub ==="
  BASE_URL="$STUB_URL" CONCURRENCY_LIST=1,2 REQUESTS_PER_CONCURRENCY=3 \
    ISL_MEAN=100 OSL_MEAN=40 ARTIFACT_DIR=/tmp/loadgen-validation/genai-perf \
    ./loadgen/genai-perf/run_sweep.sh
  echo
else
  echo "=== SKIPPING genai-perf sweep: 'genai-perf' not on PATH ==="
  echo "    (pip install genai-perf into a venv first -- see loadgen/genai-perf/README.md)"
  echo
fi

echo "=== agent harness (--engine cli) against the stub, using self-test fake agent/ ==="
python3 loadgen/agent_harness/run_harness.py \
  --base-url "${STUB_URL}/v1" --model Qwen/Qwen3.6-27B-FP8 \
  --engine cli --agent-cwd loadgen/agent_harness/_selftest/fake_agent_pkg \
  --concurrency 4 --num-sessions 12 --isl-mean 30 --osl-hint-mean 80 --seed 42 \
  --output-dir /tmp/loadgen-validation/agent-harness-cli
echo

echo "=== agent harness (--engine import) against the stub, using self-test fake agent/ ==="
python3 loadgen/agent_harness/run_harness.py \
  --base-url "${STUB_URL}/v1" --model Qwen/Qwen3.6-27B-FP8 \
  --engine import --agent-cwd loadgen/agent_harness/_selftest/fake_agent_pkg \
  --concurrency 4 --num-sessions 12 --isl-mean 30 --osl-hint-mean 80 --seed 42 \
  --output-dir /tmp/loadgen-validation/agent-harness-import
echo

echo "=== ALL LOCAL VALIDATION CHECKS PASSED (against the stub -- real endpoint runs are separate) ==="

#!/usr/bin/env bash
# loadgen/genai-perf/run_sweep.sh
#
# Parameterized genai-perf runner for direct knob-sweep benchmarking against the raw vLLM
# OpenAI-compatible endpoint. This is the recommended entrypoint (config.yaml is the base config
# it overrides -- see that file's header comment).
#
# Every SPEC.md knob this tool is responsible for -- model/endpoint, avg input length, avg output
# length, concurrent-user count -- is an environment variable with a documented default, so this
# is never hardcoded to one deployment. Loops over CONCURRENCY_LIST, issuing one
# `genai-perf profile` invocation per concurrency value, each writing to its own artifact
# subdirectory (matches the one-tagged-row-per-run shape experiment-cli (Phase 5) will consume).
#
# Usage:
#   ./run_sweep.sh
#   MODEL_ID=Qwen/Qwen3.6-27B-FP8 BASE_URL=http://<host>:8000 CONCURRENCY_LIST=1,2,4,8,16 \
#     ISL_MEAN=500 OSL_MEAN=300 ./run_sweep.sh
#
# Requires: genai-perf installed (pip install genai-perf; see README.md "Setup" -- it needs its
# own venv on Debian/Ubuntu due to PEP 668). Requires network access to Hugging Face the first
# time it resolves the tokenizer (downloads tokenizer files only, not model weights).
set -euo pipefail

# --- Knobs (SPEC.md) -- override any of these via environment variables -----------------------
MODEL_ID="${MODEL_ID:-Qwen/Qwen3.6-27B-FP8}"                # model name knob
SERVED_MODEL_NAME="${SERVED_MODEL_NAME:-$MODEL_ID}"          # what the server reports as "model"
BASE_URL="${BASE_URL:-http://localhost:8000}"                # endpoint knob -- not hardcoded to one deployment
ISL_MEAN="${ISL_MEAN:-200}"                                   # avg input length knob (tokens)
ISL_STDDEV="${ISL_STDDEV:-20}"
OSL_MEAN="${OSL_MEAN:-200}"                                   # avg output length knob (tokens)
OSL_STDDEV="${OSL_STDDEV:-20}"
CONCURRENCY_LIST="${CONCURRENCY_LIST:-1,2,4,8,16}"            # concurrent-user count knob, comma-separated
REQUESTS_PER_CONCURRENCY="${REQUESTS_PER_CONCURRENCY:-10}"    # requests measured *per concurrent user*
WARMUP_REQUESTS="${WARMUP_REQUESTS:-2}"
STREAMING="${STREAMING:-true}"                                # needed for TTFT/ITL; set false for TPS-only runs
# Endpoint type: "completions" (default) hits /v1/completions, bypassing the chat template.
# This matters for reasoning models like Qwen3.6: on the chat endpoint their thinking tokens
# stream as `reasoning_content` (not `content`), which genai-perf 0.0.16 cannot count -- every
# latency metric comes back N/A. The raw completions endpoint measures pure token generation.
# Set ENDPOINT_TYPE=chat only if the deployment's template does not emit reasoning_content.
ENDPOINT_TYPE="${ENDPOINT_TYPE:-completions}"
# ignore_eos=true forces the server to generate exactly OSL_MEAN tokens instead of stopping at
# EOS -- the standard practice for throughput benchmarking (fixed, comparable output lengths).
IGNORE_EOS="${IGNORE_EOS:-true}"
TOKENIZER="${TOKENIZER:-$MODEL_ID}"
TOKENIZER_TRUST_REMOTE_CODE="${TOKENIZER_TRUST_REMOTE_CODE:-false}"
NUM_DATASET_ENTRIES="${NUM_DATASET_ENTRIES:-100}"
ARTIFACT_DIR="${ARTIFACT_DIR:-loadgen/results/genai-perf/$(date +%Y%m%dT%H%M%S)}"
GENAI_PERF_BIN="${GENAI_PERF_BIN:-genai-perf}"
EXTRA_ARGS="${EXTRA_ARGS:-}"   # escape hatch: any additional genai-perf flags, passed verbatim

if ! command -v "$GENAI_PERF_BIN" >/dev/null 2>&1; then
  echo "ERROR: '$GENAI_PERF_BIN' not found on PATH." >&2
  echo "Install into a venv (PEP 668 blocks system-wide pip installs on this OS):" >&2
  echo "  python3 -m venv .venv-genai-perf && source .venv-genai-perf/bin/activate" >&2
  echo "  pip install genai-perf" >&2
  echo "Then re-run, or set GENAI_PERF_BIN=/path/to/venv/bin/genai-perf" >&2
  exit 1
fi

mkdir -p "$ARTIFACT_DIR"
manifest="$ARTIFACT_DIR/manifest.json"
echo "=== genai-perf knob sweep ==="
echo "model=$MODEL_ID served_model_name=$SERVED_MODEL_NAME url=$BASE_URL"
echo "isl_mean=$ISL_MEAN isl_stddev=$ISL_STDDEV osl_mean=$OSL_MEAN osl_stddev=$OSL_STDDEV"
echo "concurrency_list=$CONCURRENCY_LIST requests_per_concurrency=$REQUESTS_PER_CONCURRENCY streaming=$STREAMING"
echo "endpoint_type=$ENDPOINT_TYPE ignore_eos=$IGNORE_EOS"
echo "artifact_dir=$ARTIFACT_DIR"
echo

streaming_flag=()
if [ "$STREAMING" = "true" ]; then
  streaming_flag=(--streaming)
fi

trust_remote_flag=()
if [ "$TOKENIZER_TRUST_REMOTE_CODE" = "true" ]; then
  trust_remote_flag=(--tokenizer-trust-remote-code)
fi

ignore_eos_flag=()
if [ "$IGNORE_EOS" = "true" ]; then
  # genai-perf --extra-inputs only accepts scalar 'name:value' pairs (nested JSON is rejected)
  ignore_eos_flag=(--extra-inputs ignore_eos:true)
fi

IFS=',' read -ra CONCURRENCIES <<< "$CONCURRENCY_LIST"

run_started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
runs_json="[]"

for c in "${CONCURRENCIES[@]}"; do
  request_count=$(( c * REQUESTS_PER_CONCURRENCY ))
  if [ "$request_count" -lt "$c" ]; then request_count="$c"; fi
  run_dir="$ARTIFACT_DIR/concurrency${c}"
  echo "--- concurrency=$c request_count=$request_count -> $run_dir ---"

  # shellcheck disable=SC2086
  "$GENAI_PERF_BIN" profile \
    -m "$MODEL_ID" \
    -u "$BASE_URL" \
    --endpoint-type "$ENDPOINT_TYPE" \
    "${streaming_flag[@]}" \
    "${ignore_eos_flag[@]}" \
    --synthetic-input-tokens-mean "$ISL_MEAN" \
    --synthetic-input-tokens-stddev "$ISL_STDDEV" \
    --output-tokens-mean "$OSL_MEAN" \
    --output-tokens-stddev "$OSL_STDDEV" \
    --num-dataset-entries "$NUM_DATASET_ENTRIES" \
    --concurrency "$c" \
    --request-count "$request_count" \
    --warmup-request-count "$WARMUP_REQUESTS" \
    --tokenizer "$TOKENIZER" \
    "${trust_remote_flag[@]}" \
    --artifact-dir "$run_dir" \
    $EXTRA_ARGS

  csv="$run_dir/${SERVED_MODEL_NAME//\//_}-openai-${ENDPOINT_TYPE}-concurrency${c}/profile_export_genai_perf.csv"
  echo "  -> $([ -f "$csv" ] && echo "$csv" || echo "(csv path pattern may differ -- see $run_dir)")"
  echo
done

cat > "$manifest" <<EOF
{
  "tool": "genai-perf",
  "started_at": "$run_started_at",
  "model_id": "$MODEL_ID",
  "served_model_name": "$SERVED_MODEL_NAME",
  "base_url": "$BASE_URL",
  "isl_mean": $ISL_MEAN,
  "isl_stddev": $ISL_STDDEV,
  "osl_mean": $OSL_MEAN,
  "osl_stddev": $OSL_STDDEV,
  "concurrency_list": "$CONCURRENCY_LIST",
  "requests_per_concurrency": $REQUESTS_PER_CONCURRENCY,
  "streaming": $STREAMING,
  "endpoint_type": "$ENDPOINT_TYPE",
  "ignore_eos": $IGNORE_EOS,
  "artifact_dir": "$ARTIFACT_DIR"
}
EOF

echo "=== sweep complete. Manifest: $manifest ==="
echo "Per-concurrency metrics: $ARTIFACT_DIR/concurrency<N>/.../profile_export_genai_perf.{csv,json}"

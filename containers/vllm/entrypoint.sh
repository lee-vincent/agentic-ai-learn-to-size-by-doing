#!/usr/bin/env bash
# entrypoint.sh — builds and execs a `vllm serve` invocation from environment
# variables and/or a mounted config file. See containers/vllm/README.md for
# the full knob reference and precedence rules.
#
# Precedence (highest wins): mounted config file  >  `docker run -e` env vars
# >  built-in defaults baked into this script.
set -euo pipefail

log() { echo "[entrypoint] $*" >&2; }

# ---------------------------------------------------------------------------
# 1. Load a mounted config file, if present. It is just a shell file of
#    `KEY=value` lines (same variable names as below) — sourcing it lets a
#    mounted file override whatever came in via `docker run -e`.
# ---------------------------------------------------------------------------
: "${VLLM_CONFIG_FILE:=/etc/vllm/config.env}"
if [ -f "$VLLM_CONFIG_FILE" ]; then
  log "loading config file: $VLLM_CONFIG_FILE"
  set -a
  # shellcheck disable=SC1090
  source "$VLLM_CONFIG_FILE"
  set +a
else
  log "no config file at $VLLM_CONFIG_FILE (not required — using env vars / defaults)"
fi

# ---------------------------------------------------------------------------
# 2. Defaults. Every one of these is a single-GPU / L40S (44.7 GiB) sizing
#    decision — see README.md "Sizing rationale" for the math behind each.
# ---------------------------------------------------------------------------

# Model + precision. Qwen/Qwen3.6-27B-FP8 is Qwen's own published, pre-quantized
# checkpoint (fine-grained FP8, block size 128) — not an on-the-fly quantization.
: "${MODEL_ID:=Qwen/Qwen3.6-27B-FP8}"

# Optional explicit quantization method override. Leave empty for checkpoints
# that self-describe their quantization via config.json's quantization_config
# (true for Qwen/Qwen3.6-27B-FP8 — vLLM auto-detects `quant_method: fp8`).
# Set this if you point MODEL_ID at a community AWQ/GPTQ checkpoint that
# needs an explicit hint, e.g. QUANTIZATION=awq.
: "${QUANTIZATION:=}"

# Served model name exposed via the OpenAI API `model` field. Defaults to
# MODEL_ID itself (what most OpenAI clients expect to pass straight through).
: "${SERVED_MODEL_NAME:=$MODEL_ID}"

# KV-cache / memory knobs sized for a single L40S (44.7 GiB VRAM):
#   Qwen3.6-27B-FP8 weights: 28.75 GiB, verified against the actual
#     .safetensors blob sizes on huggingface.co/Qwen/Qwen3.6-27B-FP8 (not a
#     third-party estimate) -- see README.md "Sizing rationale".
#   gpu-memory-utilization 0.90 -> vLLM is allowed to use ~40.2 GiB total
#     (weights + activations + KV cache), leaving ~4.5 GiB headroom for the
#     CUDA context / driver / other host processes.
#   That leaves roughly 40.2 - 28.75 = ~11.5 GiB for KV cache + activations.
#   Qwen3.6-27B is a hybrid Gated-DeltaNet/attention model (full attention
#     every 4th of 64 layers = 16 full-attention layers; linear-attention
#     recurrent state elsewhere), so its KV cache grows with context length
#     much more slowly than a dense full-attention model of the same size --
#     only those 16 layers hold a token-indexed KV cache (measured: 64 KiB/
#     token at kv-cache-dtype=auto/bf16, i.e. ~2 GiB for one 32K-token
#     sequence); the rest hold a fixed-size-per-sequence recurrent state that
#     scales with max-num-seqs, not context length. max-model-len=32768 is a
#     conservative default that leaves comfortable concurrency headroom
#     inside that ~11.5 GiB; vLLM's own startup log ("# GPU blocks", "GPU KV
#     cache size") is the authority -- always check it after changing these
#     values, per SPEC.md's KV-cache-hit-rate / memory-verification
#     requirement.
: "${GPU_MEMORY_UTILIZATION:=0.90}"
: "${MAX_MODEL_LEN:=32768}"
: "${MAX_NUM_SEQS:=64}"
: "${KV_CACHE_DTYPE:=auto}"

# KV-cache management strategy knobs (SPEC.md <knobs>: PagedAttention is
# always on in vLLM; these two are the operator-facing toggles).
: "${ENABLE_PREFIX_CACHING:=true}"
: "${ENABLE_CHUNKED_PREFILL:=true}"

# Tool calling: load-bearing for agent-builder's tool loop. Without both
# flags vLLM will not emit OpenAI-format tool_calls at all.
: "${ENABLE_TOOL_CALLING:=true}"
: "${TOOL_CALL_PARSER:=qwen3_coder}"

# Reasoning: surfaces <think>...</think> content as a separate
# `reasoning_content` field instead of inline text, which is what makes the
# reasoning-level/effort knob observable per-request.
: "${REASONING_PARSER:=qwen3}"

# Qwen3.6-27B is nominally a VLM (vision tower for image/video input). This
# lab only exercises text/agentic traffic, so by default we skip loading the
# vision encoder entirely (--language-model-only) to free that memory for
# KV cache. Set to "false" to serve multimodal requests too.
: "${LANGUAGE_MODEL_ONLY:=true}"

# Network
: "${HOST:=0.0.0.0}"
: "${PORT:=8000}"

# Hugging Face auth. Qwen/Qwen3.6-27B-FP8 is NOT gated, so this is optional —
# only set HF_TOKEN if you point MODEL_ID at a gated repo.
: "${HF_TOKEN:=}"

# Model weight cache — mount this to persistent storage (the g6e.2xlarge
# host's 300 GiB EBS root, e.g. /opt/models) so weights survive container
# restarts and aren't re-downloaded every run.
: "${MODEL_CACHE_DIR:=/root/.cache/huggingface}"

# Free-form escape hatch for anything not covered above (e.g. speculative
# decoding config, --hf-overrides, rope scaling for long context). Appended
# to the command line verbatim via `eval`, so quote embedded JSON exactly as
# you would on a shell command line. Only set this from trusted config —
# it is not sanitized.
: "${EXTRA_VLLM_ARGS:=}"

export HF_HOME="$MODEL_CACHE_DIR"
if [ -n "$HF_TOKEN" ]; then
  export HUGGING_FACE_HUB_TOKEN="$HF_TOKEN"
  export HF_TOKEN
fi

mkdir -p "$MODEL_CACHE_DIR"

# ---------------------------------------------------------------------------
# 3. Assemble the vllm serve command.
# ---------------------------------------------------------------------------
cmd=(vllm serve "$MODEL_ID"
  --host "$HOST"
  --port "$PORT"
  --served-model-name "$SERVED_MODEL_NAME"
  --gpu-memory-utilization "$GPU_MEMORY_UTILIZATION"
  --max-model-len "$MAX_MODEL_LEN"
  --max-num-seqs "$MAX_NUM_SEQS"
  --kv-cache-dtype "$KV_CACHE_DTYPE"
  --reasoning-parser "$REASONING_PARSER"
)

if [ -n "$QUANTIZATION" ]; then
  cmd+=(--quantization "$QUANTIZATION")
fi

if [ "$(echo "$ENABLE_TOOL_CALLING" | tr '[:upper:]' '[:lower:]')" = "true" ]; then
  cmd+=(--enable-auto-tool-choice --tool-call-parser "$TOOL_CALL_PARSER")
fi

if [ "$(echo "$ENABLE_PREFIX_CACHING" | tr '[:upper:]' '[:lower:]')" = "true" ]; then
  cmd+=(--enable-prefix-caching)
else
  cmd+=(--no-enable-prefix-caching)
fi

if [ "$(echo "$ENABLE_CHUNKED_PREFILL" | tr '[:upper:]' '[:lower:]')" = "true" ]; then
  cmd+=(--enable-chunked-prefill)
else
  cmd+=(--no-enable-chunked-prefill)
fi

if [ "$(echo "$LANGUAGE_MODEL_ONLY" | tr '[:upper:]' '[:lower:]')" = "true" ]; then
  cmd+=(--language-model-only)
fi

log "Model:                 $MODEL_ID (served as: $SERVED_MODEL_NAME)"
log "Quantization override:  ${QUANTIZATION:-<auto-detect from checkpoint>}"
log "gpu-memory-utilization: $GPU_MEMORY_UTILIZATION"
log "max-model-len:          $MAX_MODEL_LEN"
log "max-num-seqs:           $MAX_NUM_SEQS"
log "kv-cache-dtype:         $KV_CACHE_DTYPE"
log "prefix-caching:         $ENABLE_PREFIX_CACHING"
log "chunked-prefill:        $ENABLE_CHUNKED_PREFILL"
log "tool-calling:           $ENABLE_TOOL_CALLING (parser=$TOOL_CALL_PARSER)"
log "reasoning-parser:       $REASONING_PARSER"
log "language-model-only:    $LANGUAGE_MODEL_ONLY"
log "HF cache dir:           $MODEL_CACHE_DIR"
log "extra args:             ${EXTRA_VLLM_ARGS:-<none>}"

if [ -n "$EXTRA_VLLM_ARGS" ]; then
  # Deliberate eval: lets EXTRA_VLLM_ARGS carry quoted JSON payloads
  # (e.g. --speculative-config '{"method":"mtp","num_speculative_tokens":2}')
  # exactly as they'd be typed on a shell command line.
  eval "cmd+=($EXTRA_VLLM_ARGS)"
fi

log "Launching: ${cmd[*]}"
exec "${cmd[@]}"

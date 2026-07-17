# containers/vllm — Qwen3.6-27B on a single L40S GPU

Phase 2 of the GPU/HPC Sizing Lab (see repo-root `SPEC.md` / `GOALS.md`). Scope for this
container: **one model, one GPU** — Qwen3.6-27B, served on a single `g6e.2xlarge`
(1× L40S, 44.7 GiB VRAM, 8 vCPU, 64 GiB RAM, 300 GiB EBS root). No tensor/pipeline/data/expert
parallelism, no Ray.

## What's here

| File | Purpose |
|---|---|
| `Dockerfile` | Layers `entrypoint.sh` onto the pinned official `vllm/vllm-openai:v0.25.1` image. |
| `entrypoint.sh` | Reads env vars / mounted config file, builds and `exec`s the `vllm serve` command. |
| `config/config.env.example` | Template for the mounted-config-file path. |
| `docker-compose.yml` | Primary run path for the target host (env-var driven). |
| `docker-compose.config-file.yml` | Optional overlay that mounts a real config file instead. |
| `systemd/vllm.service` | Alternative to Compose — plain `docker run` managed by systemd. |
| `verify_endpoint.sh` | curl-based smoke test to run **on the GPU host** once the server is up. |

## Model + precision: what we verified, not assumed

- **Model ID**: `Qwen/Qwen3.6-27B-FP8` — this is **Qwen's own published, pre-quantized FP8
  checkpoint**, not an on-the-fly quantization we're asked to produce ourselves. Confirmed live
  against the Hugging Face API (`GET /api/models/Qwen/Qwen3.6-27B-FP8`) on 2026-07-16:
  `quantization_config: {"quant_method": "fp8", "activation_scheme": "dynamic", "fmt": "e4m3"}`,
  fine-grained block-128 FP8 per the model card, "performance metrics are nearly identical to
  those of the original model."
- **Weight footprint — measured, not estimated**: summed the actual `.safetensors` blob sizes
  from the HF API (`?blobs=true`) for `Qwen/Qwen3.6-27B-FP8`: **28.75 GiB** (30,866,866,928 bytes
  across the language-model shards + a `mtp.safetensors` speculative head + an `outside.safetensors`
  containing embeddings/vision tower). This matches SPEC.md's ~29 GiB estimate almost exactly and
  is the number the sizing math below uses.
- **Architecture**: Qwen3.6-27B is a **hybrid Gated-DeltaNet + attention model** (`model_type:
  qwen3_5`, `architectures: ["Qwen3_5ForConditionalGeneration"]`) — full attention only every 4th
  layer (16 of 64 layers; `full_attention_interval: 4`), linear-attention recurrent state
  elsewhere. It is nominally a VLM (vision tower for image/video input; `pipeline_tag:
  image-text-to-text`), but this lab only drives text/agentic traffic — see `LANGUAGE_MODEL_ONLY`
  below. This matters for both vLLM support and KV-cache sizing (below).
- **INT4/AWQ path**: **No official Qwen-published INT4/AWQ/GPTQ checkpoint exists for
  Qwen3.6-27B** as of this writing (checked the HF API for `Qwen3.6-27B-AWQ` / `-GPTQ` / `-INT4`
  under the `Qwen` org specifically — none). Reputable community quantizations exist (e.g.
  `QuantTrio/Qwen3.6-27B-AWQ`) if you want to exercise the INT4 knob; `MODEL_ID` +
  `QUANTIZATION=awq` supports pointing at one (see `config/config.env.example`). Treat
  community checkpoints as un-vetted for anything beyond this lab.

## Base image choice

Based on `vllm/vllm-openai:v0.25.1` (Docker Hub, pulled and inspected 2026-07-16 — 18.7 GiB
uncompressed) rather than building vLLM from source. Qwen3.6-27B's hybrid Gated-DeltaNet
architecture needs vLLM's Mamba/linear-attention kernels (`vllm.model_executor.layers.mamba`,
etc.) that are already compiled into this image against a matched CUDA/PyTorch/FlashInfer stack;
reproducing that from source would add real build complexity for no benefit here. **Pinned
version: `v0.25.1`** — the `Qwen/Qwen3.6-27B-FP8` model card recommends `vllm>=0.19.0`, and 0.25.1
was the latest stable tag on Docker Hub / PyPI at build time.

## Load-bearing flags — verified against the pinned vLLM version, not assumed

Ran `docker run --gpus all --entrypoint vllm vllm/vllm-openai:v0.25.1 serve --help=all` and cross-
checked the installed package source directly. Confirmed for vLLM **0.25.1**:

| Flag | Confirmed valid value(s) | Where confirmed |
|---|---|---|
| `--enable-auto-tool-choice` | boolean flag | `serve --help=all` output |
| `--tool-call-parser` | `qwen3_coder` is a listed choice (along with ~30 others) | `serve --help=all`: `--tool-call-parser {...,qwen3_coder,...}` |
| `--reasoning-parser` | `qwen3` is a registered lazy module name | `vllm/reasoning/__init__.py`: `"qwen3": ("qwen3_engine_reasoning_parser", "Qwen3ParserReasoningAdapter")` |
| `--language-model-only` | boolean flag, default `False` | `serve --help=all`: "disables all multimodal inputs by setting all modality limits to 0" |
| `--gpu-memory-utilization` | float 0-1, base default `0.92` | `serve --help=all` |
| `--max-model-len` | int (supports `k`/`m`/`g` suffixes, or `-1`/`auto`) | `serve --help=all` |
| `--kv-cache-dtype` | `auto` (among `bfloat16,float16,fp8,fp8_e4m3,fp8_e5m2,...`) | `serve --help=all` |
| `--enable-prefix-caching` / `--no-enable-prefix-caching` | boolean pair | `serve --help=all` |
| `--enable-chunked-prefill` / `--no-enable-chunked-prefill` | boolean pair | `serve --help=all` |
| `--max-num-seqs` | int | `serve --help=all` |
| `--served-model-name` | string(s) | `serve --help=all` |
| `--quantization` / `-q` | string, auto-detected from checkpoint if unset | `serve --help=all` |

This matches exactly what the `Qwen/Qwen3.6-27B-FP8` model card's own vLLM Quickstart section
recommends (`--reasoning-parser qwen3`, and `--enable-auto-tool-choice --tool-call-parser
qwen3_coder` for tool use), so the flags aren't just "still present" in 0.25.1 — they're the
flags Qwen itself is currently telling people to use for this exact model.

We also went one step further and had vLLM itself parse our exact assembled flag set end-to-end
(see "Verification" below): it resolved `Qwen/Qwen3.6-27B-FP8` to architecture
`Qwen3_5ForConditionalGeneration`, auto-detected `quantization=fp8` from the checkpoint's own
config (no `--quantization` override needed for the default path), and only stopped at the GPU
memory pre-flight check — i.e. every flag we pass parsed and was accepted; the only failure was
"this dev box's GPU is too small," which is expected and correct.

## Endpoints

Confirmed by reading the installed vLLM 0.25.1 source (`vllm/entrypoints/openai/api_server.py`'s
`build_app` → `register_vllm_serve_api_routers` → `register_instrumentator_api_routers`), not
assumed from prior-version knowledge:

- `POST /v1/chat/completions` — OpenAI-compatible chat completions (`vllm/entrypoints/openai/chat_completion/api_router.py`)
- `GET /v1/models` — OpenAI-compatible model listing
- `GET /health` — liveness/readiness (200 healthy, 503 if the engine is dead)
- `GET /metrics` — vLLM's native Prometheus metrics endpoint (`vllm/entrypoints/serve/instrumentator/metrics.py`, mounted via `Mount("/metrics", make_asgi_app(...))`)
- `GET /version`, `GET /load` — bonus instrumentation endpoints, same router registration

**`/metrics` series relevant to SPEC.md's metric list** (exact names read out of
`vllm/v1/metrics/loggers.py` for this pinned version):
`vllm:time_to_first_token_seconds` (TTFT), `vllm:inter_token_latency_seconds` (ITL),
`vllm:e2e_request_latency_seconds` (TAT proxy at the server), `vllm:request_prompt_tokens` /
`vllm:request_generation_tokens` (input/output length), `vllm:kv_cache_usage_perc`,
`vllm:prefix_cache_queries` / `vllm:prefix_cache_hits` (**KV cache hit rate** — divide hits by
queries), `vllm:num_requests_running` / `vllm:num_requests_waiting` (concurrency).

## Sizing rationale (44.7 GiB L40S)

```
Total VRAM                          44.7 GiB
gpu-memory-utilization (default)     0.90   ->  40.23 GiB budget for weights+activations+KV cache
Qwen3.6-27B-FP8 weights (measured)  28.75 GiB
------------------------------------------------------------------------------
Remaining for KV cache + activation ~11.5 GiB
```

Qwen3.6-27B's KV cache grows far more slowly with context than a same-size dense-attention model
because only 16 of its 64 layers (`full_attention_interval: 4`) hold a token-indexed KV cache; the
other 48 hold a fixed-size-per-sequence recurrent (linear-attention) state that scales with
concurrency (`--max-num-seqs`), not context length. For the 16 full-attention layers
(`num_key_value_heads=4`, `head_dim=256`, verified from the model's own `config.json`):

```
bytes/token = 16 layers × 2 (K+V) × 4 kv_heads × 256 head_dim × 2 bytes (bf16, kv-cache-dtype=auto)
            = 65,536 bytes/token = 64 KiB/token
```

At the default `MAX_MODEL_LEN=32768`, one full-length sequence's growing KV cache is
`32768 × 64 KiB ≈ 2 GiB` — comfortably inside the ~11.5 GiB budget with room for the recurrent
state and multiple concurrent sequences. **This is a conservative starting default, not a
verified ceiling** — vLLM's own startup log (`# GPU blocks`, KV cache size, and any "Free memory
on device..." pre-flight message) is the authority once this actually runs on the L40S; see
`GOALS.md`/`SPEC.md`'s requirement to confirm via vLLM's own memory log / `nvidia-smi` rather than
trust these defaults blindly. Raise `MAX_MODEL_LEN` / `MAX_NUM_SEQS` and re-check that log if you
need more context or concurrency headroom for a given experiment.

Note: prefix caching for the Mamba/linear-attention cache's "align" mode is flagged by vLLM itself
as experimental for this architecture (confirmed in a real run — see Verification below); if you
see instability specifically correlated with `--enable-prefix-caching` on this model, that's the
first thing to try disabling.

## Configuration reference

Every knob is an environment variable, with a mounted config file able to override it (see
Precedence). Full defaults and comments live in `entrypoint.sh` and
`config/config.env.example` — summarized here:

| Variable | Default | Notes |
|---|---|---|
| `MODEL_ID` | `Qwen/Qwen3.6-27B-FP8` | HF repo ID or local path |
| `QUANTIZATION` | *(empty = auto-detect)* | Set explicitly for checkpoints that need a hint (e.g. `awq`) |
| `SERVED_MODEL_NAME` | same as `MODEL_ID` | `model` field in API responses |
| `GPU_MEMORY_UTILIZATION` | `0.90` | Fraction of VRAM for weights+activations+KV cache |
| `MAX_MODEL_LEN` | `32768` | See sizing rationale above |
| `MAX_NUM_SEQS` | `64` | Max concurrent sequences per iteration |
| `KV_CACHE_DTYPE` | `auto` | KV-cache quantization knob (SPEC.md) — try `fp8_e4m3` to roughly halve KV cache bytes/token |
| `ENABLE_PREFIX_CACHING` | `true` | KV-cache management strategy knob |
| `ENABLE_CHUNKED_PREFILL` | `true` | KV-cache management strategy knob |
| `ENABLE_TOOL_CALLING` | `true` | Toggles `--enable-auto-tool-choice --tool-call-parser $TOOL_CALL_PARSER` |
| `TOOL_CALL_PARSER` | `qwen3_coder` | Per current Qwen3.6 model card |
| `REASONING_PARSER` | `qwen3` | Surfaces `reasoning_content`; reasoning on/off itself is a per-request knob (`chat_template_kwargs.enable_thinking`), or set a server-wide default via `EXTRA_VLLM_ARGS='--default-chat-template-kwargs {"enable_thinking":false}'` |
| `LANGUAGE_MODEL_ONLY` | `true` | Skips the vision tower to free memory for KV cache — this lab is text/agentic only |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | |
| `HF_TOKEN` | *(empty)* | **Optional** — Qwen3.6-27B-FP8 is NOT gated |
| `MODEL_CACHE_DIR` | `/root/.cache/huggingface` | Mount to persistent storage |
| `EXTRA_VLLM_ARGS` | *(empty)* | Escape hatch, appended via `eval` — quote embedded JSON as on a shell command line |

**Precedence (highest wins): mounted config file → `docker run -e` / Compose env vars → built-in
defaults in `entrypoint.sh`.** This satisfies "config comes from environment variables or a
mounted config file, not hardcoded" — either mechanism works standalone, and the file wins if you
use both.

## Build

```bash
cd containers/vllm
docker build -t gpu-sizing-lab-vllm:v0.25.1 .
```

## Run — on the target g6e.2xlarge host

Prerequisites on the host (infra-builder's / a human's responsibility, not this container's):
NVIDIA driver + NVIDIA Container Toolkit installed, `/opt/models` present on the 300 GiB EBS root
(created by infra `user_data`). Verify GPU passthrough works before bringing vLLM up:

```bash
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

### Option A — docker compose (recommended)

```bash
cd containers/vllm
docker compose build
docker compose up -d
docker compose logs -f vllm   # watch for "Uvicorn running on http://0.0.0.0:8000"
```

Override any knob via a real `.env` file next to `docker-compose.yml`, or shell-export before
`up`, e.g. `MAX_MODEL_LEN=65536 docker compose up -d`. To use the mounted-config-file path
instead:

```bash
cp config/config.env.example config/config.env   # edit config.env
docker compose -f docker-compose.yml -f docker-compose.config-file.yml up -d
```

### Option B — plain `docker run`

```bash
docker run -d --name vllm-qwen3_6-27b --gpus all --ipc=host \
  -p 8000:8000 \
  -v /opt/models:/root/.cache/huggingface \
  gpu-sizing-lab-vllm:v0.25.1
```

### Option C — systemd (`containers/vllm/systemd/vllm.service`)

```bash
sudo cp containers/vllm/systemd/vllm.service /etc/systemd/system/vllm.service
sudo mkdir -p /opt/models
sudo systemctl daemon-reload
sudo systemctl enable --now vllm.service
journalctl -u vllm.service -f
```

First boot downloads the ~28.75 GiB checkpoint into `/opt/models` (persists across restarts —
subsequent boots skip the download).

## Verify — on the GPU host, once the server is up

```bash
./verify_endpoint.sh                       # defaults to http://localhost:8000
./verify_endpoint.sh http://<host-ip>:8000 # from another machine
```

Expected: PASS on `/health` (200), `/v1/models` (lists `Qwen/Qwen3.6-27B-FP8`), a real
`/v1/chat/completions` response, a tool-calling round trip that includes an OpenAI-format
`tool_calls` block (proves `--enable-auto-tool-choice --tool-call-parser qwen3_coder` is wired
correctly — this is what `agent-builder`'s tool loop depends on), and `/metrics` exposing
`vllm:` series. Manual equivalent of check 3:

```bash
curl -s http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"Qwen/Qwen3.6-27B-FP8","messages":[{"role":"user","content":"Say hello"}],"max_tokens":32}'
```

Also confirm the memory story for real, per SPEC.md's requirement to check vLLM's own memory
log / `nvidia-smi` rather than trust the defaults above blindly:

```bash
docker compose logs vllm | grep -iE "gpu blocks|kv cache|free memory|gpu_memory_utilization"
nvidia-smi   # confirm VRAM used is in the ballpark of weights + KV cache budget above
```

## Verification: local (this dev box) vs. GPU host

This container was built on a dev box with **no usable GPU for this model** (an RTX 1000 Ada,
6 GiB VRAM — nowhere near enough for a 28.75 GiB checkpoint). Everything below was genuinely run;
nothing is asserted without having actually executed it.

**Verified locally (this repo, this session):**
- `docker build` succeeds against the pinned `vllm/vllm-openai:v0.25.1` base image.
- `docker compose config` and `docker compose -f docker-compose.yml -f docker-compose.config-file.yml config` both parse cleanly.
- `systemd-analyze verify systemd/vllm.service` reports no errors.
- `entrypoint.sh` correctly assembles the `vllm serve` command line from: (a) built-in defaults,
  (b) `docker run -e` overrides (including switching to a community AWQ checkpoint +
  `--quantization awq`), (c) a mounted config file taking precedence over both, and (d)
  `EXTRA_VLLM_ARGS` correctly passing a quoted JSON payload (e.g. `--speculative-config
  '{"method":"mtp",...}'`) through as a single argument. All checked by substituting a fake
  `vllm` shim on `PATH` that just echoes its argv.
- **Ran the real `vllm` CLI (not a shim), with `--gpus all`, against our exact assembled flag
  set**, against the real `Qwen/Qwen3.6-27B-FP8` checkpoint: it fetched the tokenizer/config from
  Hugging Face, resolved `architectures: Qwen3_5ForConditionalGeneration`, auto-detected
  `quantization=fp8` from the checkpoint config with no explicit `--quantization` needed, printed
  the exact "Mamba cache mode is set to 'align'... experimental" warning this README flags above,
  and only stopped at the GPU memory pre-flight check
  (`ValueError: Free memory on device cuda:0 (4.95/6.0 GiB) ... is less than desired GPU memory
  utilization (0.9, 5.4 GiB)`) — i.e. every flag parsed, the model resolved, quantization
  auto-detected correctly, and the only failure was this dev GPU being too small, exactly as
  expected.
- Confirmed `--tool-call-parser qwen3_coder`, `--reasoning-parser qwen3`,
  `--language-model-only`, `--gpu-memory-utilization`, `--max-model-len`, `--kv-cache-dtype`,
  `--enable-prefix-caching`/`--enable-chunked-prefill`, `--served-model-name`, `--quantization`
  are all real, currently-valid flags for vLLM 0.25.1 by reading `vllm serve --help=all` and the
  installed package source directly (not assumed from memory/prior versions).
- Confirmed `/health`, `/metrics`, `/v1/chat/completions`, `/v1/models` are all real routes
  mounted by `vllm serve` in 0.25.1 by reading `vllm/entrypoints/openai/api_server.py`'s route
  registration chain, and confirmed the exact Prometheus series names
  (`vllm:time_to_first_token_seconds`, `vllm:prefix_cache_hits`, etc.) in
  `vllm/v1/metrics/loggers.py`.
- Confirmed `Qwen/Qwen3.6-27B-FP8` is Qwen's own published FP8 checkpoint (not community) and
  measured its actual weight footprint (28.75 GiB) from Hugging Face's own blob metadata API —
  not a third-party estimate.
- Confirmed no official Qwen INT4/AWQ/GPTQ checkpoint exists for Qwen3.6-27B via the HF API.

**NOT verified — genuinely requires the g6e.2xlarge GPU host, not attempted/faked here:**
- Actually loading the 28.75 GiB checkpoint onto an L40S and completing engine init.
- The real KV-cache size vLLM reports at startup on real 44.7 GiB VRAM (the ~11.5 GiB estimate
  above is arithmetic from measured weight size + documented architecture, not an observed vLLM
  log line).
- A real end-to-end `curl` to `/v1/chat/completions` returning a generated completion.
- A real tool-calling round trip returning an OpenAI-format `tool_calls` block from actual model
  inference (the flag wiring is confirmed; the model actually choosing to call the tool and vLLM
  correctly parsing that into `tool_calls` needs a live run).
- Real GPU/VRAM utilization numbers, TTFT/ITL/throughput under load, and prefix-cache hit-rate
  behavior in practice (including whether the "align" mode experimental warning above translates
  into an actual problem).
- NVIDIA Container Toolkit / `--gpus all` passthrough on the actual g6e.2xlarge AMI (infra-builder
  scope, assumed present per this task's brief).

**Recommendation**: invoke the `checker` subagent for the static/build-time portions above now;
its verdict on the runtime portions (curl returns 200 with a valid completion, tool_calls present,
memory fits with headroom) will need to happen once the g6e.2xlarge instance is actually up —
`checker`'s own instructions already anticipate this ("if you can't verify a condition without a
paid or destructive action, say so explicitly").

## Known risk to watch for on first real run

`--enable-prefix-caching` + this hybrid architecture's Mamba-cache "align" mode is explicitly
called out by vLLM itself as experimental (seen in our real dry run's log output). If the first
real serve on the L40S is unstable, set `ENABLE_PREFIX_CACHING=false` as the first thing to try
before assuming something else is wrong.

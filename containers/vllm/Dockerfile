# containers/vllm/Dockerfile
#
# Single vLLM serving image for the GPU/HPC Sizing Lab, Phase 2 scope:
# Qwen3.6-27B on one L40S GPU (target host: g6e.2xlarge, 44.7 GiB VRAM).
#
# We deliberately base on vLLM's official published OpenAI-compatible server
# image rather than building vLLM from source: it already ships a matched
# CUDA/PyTorch/FlashInfer/FlashAttention stack (including the custom kernels
# the Qwen3.5/3.6 hybrid Gated-DeltaNet + attention architecture needs), and
# building vLLM from source specifically to reproduce that is not warranted
# here. Everything model/precision/KV-cache-specific is layered on top via
# entrypoint.sh, driven by environment variables or a mounted config file —
# never hardcoded into the image.
#
# Pinned version: v0.25.1 (latest stable as of build time; the Qwen3.6-27B-FP8
# model card recommends vllm>=0.19.0 — 0.25.1 comfortably clears that and is
# the version whose `vllm serve --help` output this image's flags were
# checked against; see README.md "Verification" section).
ARG VLLM_IMAGE_TAG=v0.25.1
FROM vllm/vllm-openai:${VLLM_IMAGE_TAG}

LABEL org.opencontainers.image.title="gpu-sizing-lab-vllm" \
      org.opencontainers.image.description="vLLM OpenAI-compatible server for Qwen3.6-27B on a single L40S GPU" \
      org.opencontainers.image.source="containers/vllm"

# Model ID, precision, KV-cache strategy, and every other serving knob are
# supplied at `docker run` time via env vars / a mounted config file — see
# entrypoint.sh and README.md. Nothing model-specific is baked in here.
COPY entrypoint.sh /usr/local/bin/vllm-entrypoint.sh
RUN chmod +x /usr/local/bin/vllm-entrypoint.sh

# Prometheus metrics + OpenAI-compatible API both live on this one port.
EXPOSE 8000

# Model weight cache. Mount persistent storage here (the g6e.2xlarge host's
# 300 GiB EBS root, e.g. `-v /opt/models:/root/.cache/huggingface`) so
# weights survive container restarts. MODEL_CACHE_DIR (see entrypoint.sh)
# controls the in-container path if you want somewhere other than the
# default HF cache location.
VOLUME ["/root/.cache/huggingface"]

# The base image's own ENTRYPOINT is ["vllm", "serve"] with a null CMD
# (verified via `docker inspect vllm/vllm-openai:v0.25.1`). We override the
# ENTRYPOINT entirely: our script builds the full `vllm serve ...`
# invocation from env/config and execs it, so no args need to be baked into
# CMD.
ENTRYPOINT ["/usr/local/bin/vllm-entrypoint.sh"]
CMD []

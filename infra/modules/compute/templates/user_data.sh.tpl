#!/bin/bash
# Best-effort bring-up: record GPU/driver status and prep a directory for
# model weights for later phases to use. Nothing here should block boot
# (serving-builder in Phase 2 owns actually running vLLM). No EFA, no FSx,
# no multi-node coordination -- this is a single instance.
set -u
LOG=/var/log/user-data.log
exec > >(tee -a "$LOG") 2>&1
echo "=== user_data start: $(date -u) ==="

echo "--- instance identity ---"
echo "gpu_instance_type=${gpu_instance_type}"

echo "--- NVIDIA driver check ---"
nvidia-smi -L || echo "WARNING: nvidia-smi not available yet"

echo "--- model weights directory ---"
# Model weights (Qwen3.6-27B FP8, ~29 GiB) live directly on the root EBS
# volume -- no FSx/S3 staging step, since a single node just downloads
# straight from the HuggingFace Hub. serving-builder (Phase 2) owns actually
# populating this path.
mkdir -p /opt/models
echo "created /opt/models for serving-builder to populate (e.g. via huggingface-cli download)"

echo "=== user_data end: $(date -u) ==="

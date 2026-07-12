# SPEC — GPU/HPC Sizing Lab

## `<metrics>`
- CPU Utilization
- CPU RAM Utilization
- GPU Utilization
- GPU VRAM Utilization
- Inter-Token Latency (ITL): time between generating consecutive tokens
- Tokens Per Second (TPS): total throughput speed
- Latency Per Output Token: total generation time divided by total output tokens
- Turnaround Time (TAT): total clock time from user submission to final token delivery — measure
  this at the client/agent layer, since it spans the full round trip including any agent
  reasoning/tool steps, not just raw token generation

## `<knobs>`
- Different LLMs with different parameter counts — small (~7–8B), medium (~13–14B), large (~70B),
  plus one MoE model specifically to exercise expert parallelism
- Parameter precision (FP8 down to INT4)
- Serving framework (vLLM vs. NVIDIA NIM)
- KV-cache management strategy (PagedAttention, prefix caching, chunked prefill, KV cache
  quantization)
- Average input length and output length
- Number of concurrent users
- LLM parallelism: data parallel, tensor parallel, expert parallel, pipeline/model parallel
- Decoding algorithm: greedy, parallel sampling, speculative decoding, beam search — confirm
  current support level for each in vLLM and NIM respectively before assuming parity

## Architecture summary
- **Infra**: Terraform, multi-node GPU cluster in a placement group, EFA where supported (verify
  current instance-family support), shared FSx for Lustre or EFS for model weight caching.
- **Serving**: vLLM (Ray-backed for multi-node tensor/pipeline/data parallel) and NVIDIA NIM
  (NGC API key required — inject via Secrets Manager/SSM, never hardcode). Both expose an
  OpenAI-compatible `/v1/chat/completions` endpoint. NIM's multi-node model-parallelism support is
  more constrained than vLLM+Ray — keep the comparison fair, don't force NIM somewhere it isn't
  supported.
- **Agent**: a real tool-calling agent (at minimum a calculator tool and a retrieval/lookup tool)
  targeting either backend, used both as a study subject and as an agentic-shaped load source.
- **Load generation**: NVIDIA `genai-perf` for raw-endpoint knob-sweep benchmarking, plus a
  custom harness driving the agent itself for agentic-shaped traffic (multi-turn, variable output
  length, burstier concurrency) that `genai-perf` alone won't reproduce.
- **Monitoring**: Prometheus + Grafana, DCGM Exporter (GPU), Node Exporter (CPU/RAM), vLLM/NIM
  native Prometheus metrics, one dashboard combining all three metric families per experiment run.
- **Experiment control**: a CLI that toggles each knob, applies the config, runs load for a fixed
  duration, scrapes the metrics window, and appends a tagged row to a results file (CSV/Parquet),
  plus a plotting script for post-sweep comparison.

## Deliverables checklist
- [ ] Terraform modules: networking, multi-node GPU compute/cluster, shared storage, IAM/secrets
- [ ] Container build files: vLLM, NIM, custom agent, load generators
- [ ] Multi-node orchestration config (Ray or equivalent) for tensor/pipeline/data/expert parallel
- [ ] Monitoring stack: Prometheus, Grafana (dashboard JSON), DCGM Exporter, Node Exporter
- [ ] Experiment control CLI + structured results storage + comparison plotting
- [ ] Cost visibility: running-instance/cost banner every session, notify-only billing alarm,
  single documented teardown command
- [ ] README: prerequisites (AWS quota request, NGC API key), quickstart, how to run a sweep, how
  to tear down, current approximate hourly cost for the instance types selected

## Verify while building, don't assume
- Current EFA support and current on-demand pricing for the selected GPU instance family
- Current NIM licensing/access requirements and which chosen models have NIM-optimized profiles
- Whether any chosen model (e.g. Llama-family weights) needs a gated HuggingFace token

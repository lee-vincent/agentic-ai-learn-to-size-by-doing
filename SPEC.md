# SPEC — GPU/HPC Sizing Lab

## `<metrics>`
- CPU Utilization
- CPU RAM Utilization
- GPU Utilization
- GPU VRAM Utilization
- Time to First Token (TTFT)
- Inter-Token Latency (ITL): time between generating consecutive tokens
- Tokens Per Second (TPS): total throughput speed
- Requests Per Second
- KV Cache Hit Rate
- Latency Per Output Token: total generation time divided by total output tokens
- Turnaround Time (TAT): total clock time from user submission to final token delivery — measure
  this at the client/agent layer, since it spans the full round trip including any agent
  reasoning/tool steps, not just raw token generation
- Average input and output length (observed per run, alongside the configured knob value)
- Reasoning level / effort used (observed per run)

## `<knobs>`
- Different LLMs with different parameter counts and architectures — see Model Lineup below
- Parameter precision (FP8 down to INT4)
- KV-cache management strategy (PagedAttention, prefix caching, chunked prefill, KV cache
  quantization)
- Average input length and output length
- Number of concurrent users
- LLM parallelism: data parallel, tensor parallel, expert parallel, pipeline/model parallel
- Decoding algorithm: greedy, parallel sampling, speculative decoding, beam search — confirm
  current vLLM support level for each before assuming parity across the lineup
- Reasoning level (effort): how much thinking each request demands — Qwen3.5/3.6's "thinking
  mode" is the mechanism for this knob; test thinking on vs. off and, where the model exposes it,
  different effort levels

**Serving framework is no longer a knob** — this build is vLLM-only. An earlier draft compared
vLLM against NVIDIA NIM; that comparison was dropped to simplify the build and avoid NIM's NGC
licensing overhead. If you want it back later it's a clean re-addition: stand up a second
`containers/nim/` service behind the same OpenAI-compatible interface.

## Model lineup
Three current Qwen models (all natively supported by vLLM), chosen to span parameter count,
dense-vs-MoE architecture, and single-node-vs-true-multi-node infra:

| Model | Architecture | Total / Active Params | Role in this lab |
|---|---|---|---|
| **Qwen3.6-27B** | Dense | 27B / 27B | Dense baseline, released April 2026 — the newest and strongest dense model in the family. No expert-routing variable in the mix, so it isolates the effect of precision, KV-cache strategy, and parallelism knobs from MoE routing behavior. Single-node, multi-GPU territory. |
| **Qwen3.5-35B-A3B** | MoE | 35B / 3B | Small-scale MoE — 256 experts + 1 shared expert, only ~8.6% of total params active per token. This is the cheap end of the expert-parallel knob: real MoE routing behavior, but modest enough to iterate on quickly and to compare directly against the 27B dense model at similar total-parameter scale. |
| **Qwen3.5-397B-A17B** | MoE | 397B / 17B | Flagship MoE, released Feb 2026. This is where the sizing question gets genuinely interesting, and whether it *needs* multiple nodes depends on node type and precision — don't assume it forces multi-node. Back-of-envelope: BF16 (~2 bytes/param ≈ 800 GB of weights) won't fit a single 8×80 GB node and does force multi-node; FP8 (~400 GB) and INT4 (~200 GB) both fit the weights on one 8×80 GB node, but KV-cache headroom shrinks as context length and concurrency climb. Finding exactly where single-node stops being enough — for your chosen node, precision, and context/concurrency targets — is the whole point of the lab. (Treat those GB figures as estimates to verify against the model card and vLLM's own memory reporting.) Separately, you want a multi-node cluster *regardless* of this model's footprint, because exercising pipeline and data parallel across nodes is an explicit learning goal. |

Two things to verify at build time rather than assume, since published third-party estimates
vary in reliability:
- The actual VRAM footprint of each model at each precision setting (FP8 vs. INT4 in particular)
  — check the current official model card and vLLM's own memory estimation rather than a
  third-party blog figure.
- Whether pre-quantized checkpoints (AWQ/GPTQ/FP8) are already published for each model on
  Hugging Face, or whether `serving-builder` needs to quantize from the BF16 checkpoint itself.

## Architecture summary
- **Infra**: Terraform, multi-node GPU cluster in a placement group, EFA where supported (verify
  current instance-family support), shared FSx for Lustre or EFS for model weight caching —
  important given the size of the 397B-A17B checkpoint.
- **Serving**: vLLM only, Ray-backed for multi-node tensor/pipeline/data parallel. One
  OpenAI-compatible `/v1/chat/completions` endpoint per model in the lineup, all launched from
  the same container image with the model ID and parallelism config passed in as parameters —
  not three separate container images to maintain. Two flags matter for this lab specifically
  and are easy to forget: tool calling in OpenAI format needs vLLM launched with
  `--enable-auto-tool-choice --tool-call-parser qwen3_coder`, and the reasoning-level knob only
  becomes observable with `--reasoning-parser qwen3` (both per the current Qwen model cards;
  verify against your installed vLLM version). Note these are vision-language models, so the
  checkpoint includes a vision tower — a modest addition to the weight footprint even when
  you're only serving text/agentic traffic.
- **Agent**: a real tool-calling agent (at minimum a calculator tool and a retrieval/lookup tool)
  targeting the vLLM endpoint, used both as a study subject and as an agentic-shaped load source.
- **Load generation**: NVIDIA `genai-perf` for raw-endpoint knob-sweep benchmarking, plus a
  custom harness driving the agent itself for agentic-shaped traffic (multi-turn, variable output
  length, burstier concurrency) that `genai-perf` alone won't reproduce.
- **Monitoring**: Prometheus + Grafana, DCGM Exporter (GPU), Node Exporter (CPU/RAM), vLLM's
  native Prometheus metrics (which cover TTFT, ITL, TPS, and KV cache hit rate directly), one
  dashboard combining all metric families per experiment run.
- **Experiment control**: a CLI that toggles each knob, applies the config, runs load for a fixed
  duration, scrapes the metrics window, and appends a tagged row to a results file (CSV/Parquet),
  plus a plotting script for post-sweep comparison.

## Deliverables checklist
- [ ] Terraform modules: networking, multi-node GPU compute/cluster, shared storage, IAM/secrets
- [ ] Container build files: vLLM (parameterized by model + parallelism config), custom agent,
  load generators
- [ ] Multi-node orchestration config (Ray) for tensor/pipeline/data/expert parallel
- [ ] Monitoring stack: Prometheus, Grafana (dashboard JSON), DCGM Exporter, Node Exporter
- [ ] Experiment control CLI + structured results storage + comparison plotting
- [ ] Cost visibility: running-instance/cost banner every session, notify-only billing alarm,
  single documented teardown command
- [ ] README: prerequisites (AWS quota request, HuggingFace token if any lineup model is gated),
  quickstart, how to run a sweep, how to tear down, current approximate hourly cost for the
  instance types selected

## Verify while building, don't assume
- Current EFA support and current on-demand pricing for the selected GPU instance family
- Actual VRAM footprint of each of the three lineup models at each precision setting, and whether
  pre-quantized checkpoints already exist — check current model cards, not third-party estimates
- Whether any lineup model requires a gated HuggingFace token

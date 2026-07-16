# SPEC — GPU Sizing Lab (single-node, single-GPU)

## Objective
Get **one** current Qwen model serving on **one** widely-available L40S GPU instance, generate
synthetic load, and measure how CPU/RAM/GPU/VRAM utilization and inference-quality metrics respond
as software knobs change. This is a deliberately tightened scope (see "Scope history" at the
bottom): the earlier draft aimed at a multi-node, multi-GPU, three-model cluster and kept hitting
real EC2 capacity limits. The fastest path to a working lab is one GPU, one model, no cross-node
anything.

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
These are the knobs that are meaningful on a single GPU:
- Parameter precision (FP8 and INT4/AWQ — see the note on which the L40S can hold)
- KV-cache management strategy (PagedAttention, prefix caching, chunked prefill, KV cache
  quantization)
- Average input length and output length
- Number of concurrent users
- Decoding algorithm: greedy, parallel sampling, speculative decoding, beam search — confirm
  current vLLM support level for each before assuming parity
- Reasoning level (effort): Qwen's "thinking mode" is the mechanism — test thinking on vs. off
  and, where the model exposes it, different effort levels

**Serving framework is not a knob** — this build is vLLM-only, behind an OpenAI-compatible
interface.

## Model — single model
| Model | Architecture | Params | Why this one |
|---|---|---|---|
| **Qwen3.6-27B** | Dense | 27B | Newest, strongest dense model in the family (released April 2026). Dense means no expert-routing variable, so it cleanly isolates the effect of precision, KV-cache strategy, and concurrency knobs. At FP8 it fits a single L40S with room for KV cache — which is exactly what makes it the right first (and, for now, only) target. |

Verify at build time (don't trust third-party estimates):
- **VRAM footprint at each precision.** Rough figures to confirm against the current model card
  and vLLM's own memory reporting: BF16 ≈ 52 GiB (does **not** fit one 44.7 GiB L40S), FP8 ≈ 29
  GiB (fits, ~15 GiB left for KV cache/activations), INT4/AWQ ≈ 14–15 GiB (fits with the most KV
  headroom). **FP8 is the default operating point; BF16 is not an option on a single L40S.**
- **Pre-quantized checkpoints.** Check whether an official FP8 (and/or AWQ/GPTQ INT4) checkpoint
  is already published on Hugging Face. Prefer a published FP8 checkpoint over on-the-fly
  quantization — dynamic FP8 from the BF16 weights needs the BF16 checkpoint resident during load,
  which is awkward on a 44.7 GiB card.
- Note this is a vision-language model, so the checkpoint includes a vision tower — a modest
  addition to the footprint even when serving only text/agentic traffic.

## Instance — single instance
- **`g6e.2xlarge`**: 1× NVIDIA L40S (44.7 GiB VRAM), 8 vCPU, 64 GiB RAM. On-demand **$2.24/hr**
  in us-east-1 (verified via the AWS Price List API, 2026-07-16). Chosen for low cost + wide
  availability: it's a single instance with **no cluster placement group** (placement groups, not
  the GPU itself, were the source of the earlier capacity failures). The 8 vCPU / 64 GiB RAM tier
  is the reliable floor for loading a 27B checkpoint and handling request + vision preprocessing;
  `g6e.xlarge` (4 vCPU / 32 GiB) is cheaper but risky, `g6e.4xlarge` gives more headroom if
  needed.
- Verify current on-demand pricing and L40S availability in your chosen region/AZ before applying.

## Architecture summary
- **Infra**: Terraform, one `g6e.2xlarge` in a simple VPC/subnet with a security group. Model
  weights live on the root EBS volume (sized generously) or a dedicated EBS volume — **no FSx for
  Lustre, no EFA, no placement group** (all only justified by multi-node, which is out of scope).
  IAM instance profile with SSM Session Manager access; an optional SSM SecureString slot for an
  `HF_TOKEN` (the Qwen lineup is Apache-2.0/ungated, so this is provisioned-but-optional).
- **Serving**: vLLM only, single GPU (no tensor/pipeline/data/expert parallel). One
  OpenAI-compatible `/v1/chat/completions` endpoint, model ID + precision + KV-cache strategy
  passed in as parameters. Two flags matter and are easy to forget: tool calling in OpenAI format
  needs `--enable-auto-tool-choice --tool-call-parser qwen3_coder`, and the reasoning-level knob
  only becomes observable with `--reasoning-parser qwen3` (per the current Qwen model card; verify
  against your installed vLLM version).
- **Agent**: a real tool-calling agent (at minimum a calculator tool and a retrieval/lookup tool)
  targeting the vLLM endpoint, used both as a study subject and as an agentic-shaped load source.
- **Load generation**: NVIDIA `genai-perf` for raw-endpoint knob-sweep benchmarking, plus a custom
  harness driving the agent for agentic-shaped traffic (multi-turn, variable output length,
  burstier concurrency) that `genai-perf` alone won't reproduce.
- **Monitoring**: Prometheus + Grafana, DCGM Exporter (GPU), Node Exporter (CPU/RAM), vLLM's
  native Prometheus metrics (TTFT, ITL, TPS, KV cache hit rate). One dashboard per experiment run.
  Everything runs on the single node.
- **Experiment control**: a CLI that toggles each knob, applies the config, runs load for a fixed
  duration, scrapes the metrics window, and appends a tagged row to a results file (CSV/Parquet),
  plus a plotting script for post-sweep comparison.

## Deliverables checklist
- [ ] Terraform: networking, one GPU instance, EBS for weights, IAM/instance profile (+ optional
  HF_TOKEN slot)
- [ ] vLLM container (parameterized by model + precision + KV-cache strategy), custom agent, load
  generators
- [ ] Monitoring stack: Prometheus, Grafana (dashboard JSON), DCGM Exporter, Node Exporter
- [ ] Experiment control CLI + structured results storage + comparison plotting
- [ ] Cost visibility: running-instance/cost banner every session, notify-only billing alarm,
  single documented teardown command
- [ ] README: prerequisites (AWS quota, HuggingFace token if ever needed), quickstart, how to run
  a sweep, how to tear down, current approximate hourly cost

## Verify while building, don't assume
- Current on-demand pricing and L40S availability for `g6e.2xlarge` in your region
- Actual VRAM footprint of Qwen3.6-27B at FP8 and INT4, and whether pre-quantized checkpoints
  already exist — check the current model card and vLLM's memory reporting, not a blog figure
- Whether the model requires a gated HuggingFace token (expected: no)

## Scope history — what was deliberately dropped (and how to add it back)
The earlier version of this spec targeted a multi-node, multi-GPU cluster serving three models
(Qwen3.6-27B dense, Qwen3.5-35B-A3B MoE, Qwen3.5-397B-A17B flagship MoE) and exercising data,
tensor, pipeline, and expert parallelism. That was over-ambitious for the immediate goal and ran
into real EC2 GPU capacity limits. **Out of scope for now** (each is a clean future re-addition
once the single-GPU lab is proven):
- The two MoE models and the expert-parallel knob
- Multi-node and multi-GPU parallelism (data/tensor/pipeline/expert) — and with it FSx for Lustre,
  EFA, and cluster placement groups
- The "different LLMs" knob (only one model now)

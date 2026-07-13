# Objective

Build a hands-on learning environment for sizing and configuring server hardware (CPU, CPU RAM, GPU, VRAM, NICs) for GenAI chatbot and agentic AI workloads. This means: deploy a real vLLM serving stack and a real AI agent on AWS EC2 GPU instances, generate realistic synthetic load against them, and observe how the `<metrics>` below respond as I change each of the `<knobs>` below — including the full set of LLM parallelism strategies (data, tensor, pipeline, and expert parallel), which requires a multi-node, multi-GPU cluster rather than a single instance.

I want this built fast, using Terraform for infrastructure and containers for the application layer. I understand a multi-node, full-parallelism setup with on-demand GPU instances is the most expensive way to do this — that's a deliberate choice on my part to get hands-on with every knob, not an oversight. I don't want a hard spending cap or auto-shutdown that kills my session; I do want good cost *visibility* so I always know what's running and what it's costing, plus a fast, reliable teardown path.

---

## Scope decisions (already made — build to these, don't re-ask)

1. **Parallelism scope:** Multi-GPU, **multi-node**. The build must support data parallel, tensor parallel, pipeline parallel, and expert parallel (expert parallel is exercised via the two MoE models in the lineup below).
2. **Serving framework:** **vLLM only**, fronted by an OpenAI-compatible API. (An earlier version of this project also targeted NVIDIA NIM as a second framework for comparison; that's been dropped to simplify the build and avoid NIM's NGC licensing overhead.)
3. **Cost guardrail:** No hard budget cap, no automatic instance termination. Instead:
   - Every control-script invocation prints currently-running instances, their on-demand hourly rate, and running time/cost-so-far.
   - Stand up an AWS Budget or CloudWatch billing alarm that sends a notification (email/SNS) at defined thresholds — informational only, must not auto-terminate anything.
   - Provide one clearly documented command (e.g. `make destroy` / `terraform destroy`) as the canonical teardown, called out prominently in the README.

---

## Architecture requirements

### 1. AWS infrastructure (Terraform)
- Multi-node GPU cluster (2+ nodes) in a cluster placement group, with EFA-enabled networking on the instance families where it's supported — **verify current EFA support and current on-demand pricing for whatever instance family you select**, since both change over time and shouldn't be assumed from training data.
- Shared high-throughput storage (FSx for Lustre or EFS) for model weight caching across nodes — important given the size of the Qwen3.5-397B-A17B checkpoint.
- VPC, security groups, and minimally-scoped IAM roles.
- A documented step (or automation) for requesting the GPU instance vCPU quota increase — most AWS accounts start at 0 for G/P instance families, and this is the most common reason a "fast" deploy stalls.
- Terraform variables for instance type/count so the parallelism knobs (tensor/pipeline/data/expert) can be exercised by changing a variable, not rewriting modules. Size the cluster against the largest model in the lineup (Qwen3.5-397B-A17B) rather than a rule-of-thumb estimate.
- Use an NVIDIA GPU-optimized base (e.g. the AWS Deep Learning AMI) or an equivalent container bootstrap to avoid burning time on driver/CUDA setup.

### 2. Model serving layer (containers)
- **vLLM**: one container image, parameterized by model ID, precision, and parallelism config — not a separate image per model. Configured for tensor/pipeline/data parallel across nodes (Ray-backed multi-node deployment); supports the precision knob (FP16/BF16/FP8/INT4 via AWQ/GPTQ where applicable); exposes Prometheus metrics.
- Model lineup — three current Qwen models, chosen to span parameter count and dense-vs-MoE architecture:
  - **Qwen3.6-27B** (dense, 27B) — dense baseline, isolates parallelism/precision/KV-cache effects from MoE routing behavior.
  - **Qwen3.5-35B-A3B** (MoE, 35B total / 3B active) — small-scale MoE, the cheap end of the expert-parallel knob.
  - **Qwen3.5-397B-A17B** (MoE, 397B total / 17B active) — flagship MoE, the model that actually forces true multi-node placement (tensor parallel within a node, pipeline parallel across nodes).
- Confirm whether pre-quantized checkpoints (AWQ/GPTQ/FP8) already exist for each of these three models on Hugging Face before assuming you need to quantize from BF16 yourself.

### 3. Custom agentic AI agent
- Build an actual agent with a tool-calling loop (e.g. a calculator tool and a retrieval/lookup tool are enough) — not just a chat completion wrapper. Agentic traffic has a different shape than chatbot traffic: multi-turn context growth, output length variability driven by intermediate reasoning/tool steps, and burstier concurrency. The agent needs to produce that shape so the load is representative of what it's meant to teach.
- Agent should target the vLLM endpoint, and should be able to switch which of the three lineup models it's pointed at via config, not a hardcoded name. All three models support "thinking mode" — make reasoning effort configurable too, since it's one of the knobs.
- Keep the framework lightweight (a minimal custom loop, or LangGraph if you want the structure) — the point is realistic traffic generation, not agent sophistication.

### 4. Synthetic load generation
- Use **NVIDIA `genai-perf`** for direct framework/knob-sweep benchmarking against the raw serving endpoint — it's purpose-built for TTFT/ITL/TPS/latency-style metrics and integrates with Prometheus.
- Separately, drive the custom agent itself at varying concurrency levels to capture agent-specific load patterns that a raw-endpoint benchmarking tool won't reproduce (tool-call loops, multi-turn sessions).
- Both harnesses need configurable average input length, output length, and concurrent-user count, since those are explicit knobs.

### 5. Metrics & monitoring
- Prometheus + Grafana as the stack.
- **DCGM Exporter** for GPU utilization, VRAM utilization, temperature, power.
- **Node Exporter** for CPU and CPU RAM utilization.
- vLLM's native Prometheus metrics for TTFT, ITL, TPS, latency-per-output-token, requests per second, and KV cache hit rate.
- Turnaround Time (TAT) should be measured at the client/agent layer, since it spans the full round trip including any agent reasoning/tool steps — not just raw token generation.
- Pre-built Grafana dashboard(s) showing CPU/RAM, GPU/VRAM, and inference metrics together per experiment run, so a knob change and its effect are visible side by side.

### 6. Experiment control script
- A single CLI (Python or a well-organized Bash/Make setup) to toggle each knob — model, precision, KV-cache strategy, input/output length, concurrency, parallelism strategy, decoding algorithm, reasoning effort — and automatically:
  1. Apply the config (redeploy/reconfigure containers as needed).
  2. Run the load generator for a fixed duration.
  3. Scrape/export the corresponding metrics window.
  4. Append results to a structured file (CSV or Parquet) tagged with the full knob configuration used, including observed input/output length and reasoning level for that run.
- A simple plotting script/notebook to compare metrics across sweep results after the fact.

---

## `<metrics>`
- CPU Utilization
- CPU RAM Utilization
- GPU Utilization
- GPU VRAM Utilization
- Time to First Token (TTFT)
- Inter-Token Latency (ITL): time between generating consecutive tokens
- Tokens Per Second (TPS): total throughput speed
- Requests per second
- KV Cache hit rate
- Latency Per Output Token: total generation time divided by total output tokens
- Turnaround Time (TAT): total clock time from user submission to final token delivery
- Average input and output length
- Reasoning level (effort used)

## `<knobs>`
- Different LLMs with different parameter counts and architectures (see Model lineup above: Qwen3.6-27B dense, Qwen3.5-35B-A3B small MoE, Qwen3.5-397B-A17B flagship MoE)
- Parameter precision (FP8 down to INT4)
- KV-cache management strategy (e.g. PagedAttention, prefix caching, chunked prefill, KV cache quantization)
- Average input length and output length
- Number of concurrent users
- LLM parallelism: data parallel, tensor parallel, expert parallel, pipeline/model parallel
- Decoding algorithm: greedy, parallel sampling, speculative decoding, beam search (confirm current support level for each in vLLM — not all decoding strategies are equally well supported)
- Reasoning level (effort): how much thinking each request demands

Serving framework is not a knob — this build is vLLM-only.

---

## Deliverables checklist
- [ ] Terraform modules: networking, multi-node GPU compute/cluster, shared storage, IAM/secrets
- [ ] Container build files: vLLM (parameterized by model + parallelism config), custom agent, load generators
- [ ] Multi-node orchestration config (Ray) for tensor/pipeline/data/expert parallel
- [ ] Monitoring stack: Prometheus, Grafana (with dashboard JSON), DCGM Exporter, Node Exporter
- [ ] Experiment control CLI + structured results storage + comparison plotting
- [ ] Cost visibility: running-instance/cost banner on every script run, AWS Budget/CloudWatch billing alarm (notify-only), single documented teardown command
- [ ] README covering: prerequisites (AWS quota request, HuggingFace token if any lineup model is gated), quickstart, how to run a knob sweep, how to tear everything down, and current approximate hourly cost for the instance types selected

## Things to verify rather than assume while building
- Current EFA support and current on-demand pricing for whatever GPU instance family gets selected for multi-node work
- Actual VRAM footprint of each of the three lineup models at each precision setting, and whether pre-quantized checkpoints already exist — check current model cards, not third-party estimates
- Whether any lineup model requires a gated HuggingFace token in the pipeline

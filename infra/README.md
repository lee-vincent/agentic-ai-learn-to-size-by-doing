# infra/ -- GPU/HPC Sizing Lab: networking, compute, storage, IAM/secrets

Terraform for a multi-node, multi-GPU AWS cluster sized against the actual
VRAM footprint of `Qwen3.5-397B-A17B` (the largest model in the SPEC.md
lineup) at FP8 and INT4, with `Qwen3.6-27B` and `Qwen3.5-35B-A3B` running on
a subset of the same homogeneous cluster. **This module only ever runs
`terraform init`/`validate`/`plan`. Nothing here runs `apply` or
`destroy` -- that is a deliberate, separate, human-gated step.**

```
infra/
├── main.tf, variables.tf, outputs.tf, versions.tf   # root module: wires everything together
├── terraform.tfvars.example                          # documented knobs, copy to terraform.tfvars
├── scripts/check_gpu_quota.sh                        # vCPU quota check/request helper
└── modules/
    ├── networking/   # VPC, subnet (auto-picks an AZ that offers the GPU instance type),
    │                 # IGW, security groups (EFA self-referencing rule, FSx Lustre ports,
    │                 # internal-only vLLM/Ray/Prometheus/Grafana ports)
    ├── storage/      # S3 staging bucket + FSx for Lustre (PERSISTENT_2) with a Data
    │                 # Repository Association for lazy S3->FSx hydration
    ├── iam/          # GPU-node IAM role/instance profile + the HF_TOKEN SSM parameter slot
    └── compute/      # Cluster placement group, EFA ENIs + EIPs, GPU instances (DLAMI)
```

## Instance choice: `p5.48xlarge` (8x H100 80GB)

Verified live against AWS on **2026-07-13** (region `us-east-1`), not
recalled from memory:

| Fact | Value | Source |
|---|---|---|
| EFA support | Yes -- `EfaSupported: true`, up to 32 EFA interfaces, 3200 Gbit aggregate network | `aws ec2 describe-instance-types --instance-types p5.48xlarge` |
| On-demand price | **$55.04/hr** (Linux, `us-east-1`, shared tenancy) | AWS Price List API (`aws pricing get-products --service-code AmazonEC2 ...`) |
| vCPUs / node | 192 | same `describe-instance-types` call |
| VRAM / node | 8 x 80 GiB = 640 GiB (H100) | same call |

Why `p5.48xlarge` over the alternatives actually checked:

- **`p4de.24xlarge`** (8x A100 80GB, $27.45/hr, EFA 4x100Gbit) is the
  cheapest 8x80GB option and a reasonable budget fallback, but A100's lower
  memory bandwidth and older NVLink generation (600 GB/s vs H100's 900 GB/s)
  directly handicaps the throughput-shaped metrics this lab measures
  (TPS, ITL, TTFT under concurrency) -- not just absolute cost.
- **`p5en.48xlarge`** (8x H200 141GB, $63.30/hr, EFA 3200Gbit) has enough
  VRAM per node (1128 GiB) that even the flagship model's BF16 checkpoint
  (~751 GiB) fits on a *single* node. That's a genuinely interesting
  alternative to test against once the pipeline is validated on H100, but
  it undercuts the SPEC.md narrative (BF16 forcing multi-node on an 8x80GB
  node) as a *default*, so it's noted here as a documented alternative
  rather than the default. Swap `gpu_instance_type` to try it.
- **`p6-b200.48xlarge`** (8x B200 179GB, $113.93/hr) and **`p6-b300.48xlarge`**
  (8x B300 268GB, on-demand price not returned by the Price List API at
  check time -- likely not yet GA-priced for on-demand in this account/
  region) are real, current offerings in `us-east-1` but are priced well
  beyond what a cost-conscious lab needs for this exercise.
- **`g6e.48xlarge`** (8x L40S 44.7GB, $30.13/hr, EFA but only 400Gbit) was
  checked and rejected: L40S is an inference-class GPU with far less VRAM
  per GPU and no NVLink-class intra-node fabric, so it doesn't match
  SPEC.md's explicit "8x80GB node" framing for the flagship model and would
  force awkward, non-representative parallelism configs.

`gpu_instance_type` is a variable -- switching between any of the above is
a one-line change (plus re-verifying quota/pricing, since both drift).

## VRAM footprint of Qwen3.5-397B-A17B -- verified, not estimated

SPEC.md's own back-of-envelope (BF16 ~800GB, FP8 ~400GB, INT4 ~200GB) is
explicitly flagged there as something to verify rather than trust. Verified
directly against the HuggingFace repos on 2026-07-13 (summed actual
`.safetensors` file sizes via the HF API, not a third-party blog post):

| Checkpoint | Repo | Actual weight bytes | GiB | Decimal GB |
|---|---|---|---|---|
| BF16 (original) | `Qwen/Qwen3.5-397B-A17B` | 806,796,241,352 | 751.4 | 806.8 |
| FP8 (official, pre-quantized) | `Qwen/Qwen3.5-397B-A17B-FP8` | 406,151,669,464 | 378.3 | 406.2 |
| INT4 (official GPTQ, pre-quantized) | `Qwen/Qwen3.5-397B-A17B-GPTQ-Int4` | 235,708,083,992 | 219.5 | 235.7 |

Both quantized checkpoints already exist on the Hub -- no on-the-fly
quantization needed for FP8 or INT4 for this model. Two things worth
flagging for `serving-builder`:

- The **INT4 checkpoint is a mixed-precision GPTQ quantization**, not pure
  4-bit throughout: its `quantization_config.dynamic` block explicitly
  excludes attention layers, the shared-expert MLP, MTP layers, and the
  vision tower from 4-bit quantization (`-:.*attn.*`, `-:.*shared_expert.*`,
  `-:.*mtp.*`, `-:.*visual.*`) -- only the routed-expert MLP weights are
  actually 4-bit. That's exactly why INT4 (219.5 GiB) isn't ~1/4 of BF16
  (751.4 GiB) the way a naive "4 bits vs 16 bits" estimate would suggest.
- The **FP8 checkpoint** similarly keeps small modules (embeddings,
  `lm_head`, linear-attention gates, the vision tower) at higher precision
  per its `modules_to_not_convert` list.

All of the above VL checkpoints already include the vision tower's weights
(per SPEC.md's note that these are VLMs even though this lab drives them
with text/agentic traffic) -- it's baked into the byte counts above, not an
extra to add on.

### Why this model's KV cache is *not* what a standard 60-layer transformer would suggest

`Qwen3.5-397B-A17B` is a **hybrid linear-attention architecture** (verified
from its `config.json`), not a uniform transformer:

- 60 total layers, but only **15 use full (quadratic) attention** -- one in
  every four, per `full_attention_interval: 4`. The other 45 are Gated
  DeltaNet **linear attention** layers, which carry a fixed-size recurrent
  state instead of a KV cache that grows with sequence length.
- The full-attention layers use GQA with only **2 KV heads**, head dim 256.

Full-attention KV cache per token (all 15 layers, bf16 KV cache):
`2 (K+V) x 2 kv_heads x 256 head_dim x 2 bytes x 15 layers = 30,720 bytes/token`

Gated DeltaNet recurrent state per *sequence* (not per token -- fixed size
regardless of context length): `64 value_heads x 128 key_dim x 128 value_dim
x 2 bytes x 45 layers ≈ 94 MB/sequence`.

This means the traditional "long context is expensive" framing still holds
(full-attention KV cache scales linearly with tokens), but the *constant*
per-sequence overhead of the linear-attention state (~94 MB) becomes the
dominant KV-memory cost at high concurrency + short context, rather than at
long context -- a genuinely different scaling shape than a dense
60-layer/GQA-8 transformer of similar size would have, and specifically why
a rule-of-thumb KV-cache estimate for "a 397B model" would be wrong here.

### Putting it together: does it fit `p5.48xlarge` (640 GiB/node)?

| Precision | Weights | Fits 1 node (640 GiB)? | Headroom (1 node) | Headroom (2 nodes, 1280 GiB) |
|---|---|---|---|---|
| BF16 | 751.4 GiB | **No** | -111.4 GiB | 528.6 GiB |
| FP8 | 378.3 GiB | Yes | 261.7 GiB | 901.7 GiB |
| INT4 | 219.5 GiB | Yes | 420.5 GiB | 1060.5 GiB |

This is exactly the SPEC.md back-of-envelope, now confirmed against real
checkpoint bytes: **BF16 forces multi-node on this node type; FP8 and INT4
both fit a single node's weights**, with the actual multi-node requirement
for this lab coming from the deliberate choice to exercise cross-node
pipeline/data parallel (per CLAUDE.md/SPEC.md), not from weight size alone.

Example KV-cache budgets against the single-node FP8 headroom (261.7 GiB),
including *both* components -- this is where the fixed ~94 MiB/sequence
linear-attention state actually starts to matter, not just the
concurrency x context full-attention term. Still ignores vLLM's own
activation/CUDA-graph overhead, so treat these as upper bounds to be
checked against vLLM's actual startup log once Phase 2 stands the
container up:

| Target | Full-attn KV | Linear-attn state | Total | Fits in 261.7 GiB? |
|---|---|---|---|---|
| 64 concurrent seqs x 128K context | 240.0 GiB | 5.6 GiB | 245.6 GiB | Yes -- ~16 GiB to spare |
| 256 concurrent seqs x 32K context | 240.0 GiB | 22.5 GiB | 262.5 GiB | **No** -- over by ~0.8 GiB, needs 2 nodes (or trimmed concurrency/context) even at FP8 |
| 1024 concurrent seqs x 8K context | 240.0 GiB | 90.0 GiB | 330.0 GiB | **No** -- over by ~68 GiB; at this concurrency the linear-attn state alone (90 GiB) is over a third of the entire headroom, no longer a rounding error next to the full-attention term |

All three rows hold the same "concurrency x context" full-attention product
constant (240 GiB) on purpose, to isolate the linear-attention state's
effect: at low concurrency/long context it's negligible, and at high
concurrency/short context it alone can be the difference between fitting
on one node and needing two. Finding exactly where this ceiling actually
bites for your target context/concurrency is explicitly the point of this
lab -- see SPEC.md.

## Storage: FSx for Lustre (PERSISTENT_2), not EFS

Chose FSx for Lustre over EFS because the workload is exactly what Lustre
is built for (large sequential reads of huge files, shared across many
compute nodes at once) and EFS's throughput ceiling scales with *stored*
data, which is a bad fit for a filesystem that starts empty and needs to
serve hundreds of GB immediately.

- **Deployment type: `PERSISTENT_2`**, not `SCRATCH_2`. Verified pricing
  (AWS Price List API, `us-east-1`, 2026-07-13): `SCRATCH_2`-equivalent
  (Single-AZ SSD) is $0.140/GB-month; `PERSISTENT_2` SSD at the cheapest
  throughput tier (125 MB/s/TiB) is $0.145/GB-month -- a ~3.5% premium for
  real replication and in-place capacity/throughput scaling, which is worth
  it for a filesystem that will hold hours of multi-hundred-GB downloads.
- **S3 staging bucket + `aws_fsx_data_repository_association`**: a human
  (or a future serving-builder script) can `aws s3 sync` a checkpoint into
  the bucket once; FSx lazily hydrates file content from S3 on first read
  across every node, so nodes don't each re-download the same checkpoint
  from HuggingFace independently.
- **Default capacity: 2400 GiB.** Comfortably holds the flagship model's
  FP8 (378.3 GiB) + INT4 (219.5 GiB) checkpoints plus both smaller lineup
  models' BF16 weights (51.7 GiB + 67.0 GiB, both verified against their
  HF repos too) concurrently, with room to spare. Bump
  `fsx_storage_capacity_gib` if you want the flagship BF16 checkpoint
  (751.4 GiB) cached simultaneously as well.

## HF_TOKEN secret slot

The Qwen lineup in SPEC.md is entirely Apache-2.0 and ungated, so this may
never actually be used -- but the slot is provisioned now per this build's
requirements.

- **SSM Parameter Store `SecureString`**, not Secrets Manager. Chosen
  because Secrets Manager charges ~$0.40/secret/month regardless of whether
  it's ever read, whereas Standard-tier SSM parameters have no per-parameter
  monthly fee. For a value that may sit completely unused for the project's
  entire life, avoiding a guaranteed recurring charge is the right default.
  (Secrets Manager's rotation support isn't relevant here -- a HuggingFace
  token doesn't rotate on any schedule this lab cares about.)
- **Dedicated KMS CMK** (not the shared `alias/aws/ssm` account key), so the
  IAM policy can scope `kms:Decrypt` to exactly one key ARN instead of every
  SecureString in the account.
- **Terraform creates the parameter with a placeholder value and then never
  touches it again** (`lifecycle { ignore_changes = [value] }` in
  `modules/iam/main.tf`). This is deliberate: `$HF_TOKEN` is never read into
  Terraform, no real token is ever written to any `.tf`/`.tfvars`/state
  file, and a human injecting the real value later won't have it reverted
  by a subsequent `terraform apply`.
- **Injection command** (run by a human, out-of-band, after `apply` --
  never by Terraform, and not run as part of this build):

  ```
  aws ssm put-parameter \
    --name "/gpu-sizing-lab/hf-token" \
    --type SecureString \
    --key-id "alias/gpu-sizing-lab-hf-token" \
    --value "<real token>" \
    --overwrite \
    --region us-east-1
  ```

  This exact command (with your actual parameter name/key alias/region) is
  also emitted as the `hf_token_injection_command` Terraform output.
- **IAM scope**: the GPU-node role gets exactly `ssm:GetParameter` on this
  one parameter's ARN and `kms:Decrypt`/`kms:DescribeKey` on this one CMK's
  ARN (`modules/iam/main.tf`, `hf_token_read` policy) -- nothing broader.
  The role separately gets `AmazonSSMManagedInstanceCore` (for Session
  Manager access, since SSH is closed by default) and
  `AmazonEC2ContainerRegistryReadOnly` (to pull the vLLM container image in
  Phase 2), plus a scoped read-only policy on just the model-weights S3
  bucket -- both are broader than "one secret" but are unrelated to the
  HF_TOKEN requirement specifically and are each individually still
  read-only/least-privilege for their own purpose.
- `.gitignore` already covers this correctly: `*.tfstate*` and `.terraform/`
  are ignored (so no token value from state leaks into git even though the
  placeholder currently in state is not itself a real secret), and
  `secrets.tfvars`/`*.secret.tfvars`/`.env*` are ignored for anything a
  human might use locally when running the injection command.

## GPU instance vCPU quota -- checked, and it is *not* zero but *is* insufficient

Checked live against this AWS account (`161898774946`, `us-east-1`,
2026-07-13) via `aws service-quotas get-service-quota`:

| Quota | Current value | Needed for default plan (2x `p5.48xlarge`) |
|---|---|---|
| `L-417A185B` "Running On-Demand P instances" (vCPUs) | **64** | 2 x 192 = **384** |
| `L-DB2E81BA` "Running On-Demand G and VT instances" (vCPUs) | 48 | n/a (default instance type is P-family) |

So: this account isn't at the commonly-cited "0" default, but 64 vCPUs is
still nowhere near enough for even a single `p5.48xlarge` (192 vCPUs) or
`p4d.24xlarge` (96 vCPUs) node. **A quota increase is required before
`terraform apply` will succeed** -- `terraform plan` doesn't fail on this
(it's not a plan-time check the AWS API exposes), but `RunInstances` will
reject the launch at apply time with an insufficient-quota error.

Three ways to check/act on this, in increasing order of automation:

1. **Terraform output** (read-only, computed every `plan`/`apply`, from
   `data.aws_servicequotas_service_quota` in `main.tf`):
   ```
   terraform output quota_check
   ```
2. **`infra/scripts/check_gpu_quota.sh`** -- read-only by default, only
   submits a request when you explicitly pass `--request <value>`:
   ```
   ./scripts/check_gpu_quota.sh                    # just check
   ./scripts/check_gpu_quota.sh --request 384       # request exactly enough for 2 nodes
   ./scripts/check_gpu_quota.sh --request 768       # request enough for 4 nodes (DP=2 x PP=2 headroom)
   ```
3. **Raw AWS CLI**, if you'd rather not use the script:
   ```
   aws service-quotas get-service-quota --service-code ec2 --quota-code L-417A185B
   aws service-quotas request-service-quota-increase --service-code ec2 --quota-code L-417A185B --desired-value 384
   ```

P-family quota increases are frequently reviewed manually by AWS (unlike
many "instant" quota bumps) and capacity for P5 specifically can be tight in
any given AZ regardless of quota -- budget for this taking anywhere from a
few hours to a few business days, and consider requesting enough headroom
(e.g. 768 vCPUs for a future 4-node DPxPP=2x2 setup) up front rather than
filing multiple incremental requests.

Terraform deliberately does **not** include the `aws_servicequotas_
service_quota` *resource* (which would file a real increase request as a
side effect of `terraform apply`). Requesting a quota increase is treated
here as its own explicit, human-initiated action, consistent with this
project's guardrail that cost/capacity-adjacent actions require explicit
confirmation.

## Cost estimate (for human review)

All rates verified via the **AWS Price List API**, `us-east-1`,
**2026-07-13** -- see the command history in this session for the exact
`aws pricing get-products` calls. This is the estimate for the
**default** `terraform.tfvars` (2x `p5.48xlarge`, 2400 GiB FSx
`PERSISTENT_2` @ 125 MB/s/TiB):

| Item | Rate | Qty | Hourly cost |
|---|---|---|---|
| `p5.48xlarge` on-demand | $55.04/hr | 2 | $110.08 |
| FSx for Lustre `PERSISTENT_2` SSD, 125 MB/s/TiB | $0.145/GB-month | 2400 GiB / 730 hr | $0.4767 |
| Public IPv4 (EIP), per node | $0.005/hr | 2 | $0.01 |
| **Total** | | | **≈ $110.57/hr (≈ $2,653/day if left running)** |

Not included above (usage-based, effectively $0 until you actually store
data / make requests): S3 storage for staged checkpoints, S3/FSx request
charges, data transfer out to the internet for HuggingFace downloads (FSx
Data Repository Association reads count as S3 GET requests, billed at
standard S3 rates -- negligible next to compute). None of these change the
order of magnitude; compute is >99% of the hourly cost.

Scaling notes:
- Each additional `p5.48xlarge` node: **+$55.04/hr**.
- Switching to `p4de.24xlarge` (8x A100 80GB): **$27.45/hr/node** instead --
  roughly half the cost, at the cost of throughput headroom (see "Instance
  choice" above).
- This is an on-demand estimate. No Reserved/Savings Plan/Spot pricing was
  applied -- per CLAUDE.md's cost posture, no cost cap is enforced here, but
  visibility is mandatory, hence this table.

## How to use

```
cd infra
terraform init
terraform validate
terraform plan            # review the plan + the quota_check and
                           # estimated_hourly_cost_usd outputs before doing
                           # anything else

# NEVER run from an agent loop -- apply is an explicit human action:
# terraform apply
```

Before `apply`:
1. Confirm/request the vCPU quota increase (see above) -- `apply` will fail
   without it.
2. Review `terraform plan`'s resource count and the cost table above.
3. Decide whether you actually want the HF_TOKEN slot populated (see
   injection command above) -- it's fine to leave it as the placeholder
   indefinitely since the Qwen lineup is ungated.

## Assumptions and things not fully verified

- **DLAMI EFA driver bundling**: `modules/compute` boots nodes from
  `/aws/service/deeplearning/ami/x86_64/base-oss-nvidia-driver-gpu-ubuntu-22.04/latest/ami-id`,
  whose description (verified live) explicitly lists P4d/P4de/P5/P5en/
  P6-B200/P6-B300 support and ships the NVIDIA driver + Docker + NVIDIA
  Container Toolkit. I could not independently confirm from the API alone
  that the EFA kernel driver/libfabric are preinstalled on this specific
  AMI variant (as opposed to needing the separate EFA installer) --
  `user_data.sh.tpl` runs `fi_info -p efa` at boot and logs a clear warning
  to `/var/log/user-data.log` if it's missing, and the AMI's linked release
  notes page should be checked before a real apply if this matters to you.
- **P5 capacity**: quota is necessary but not sufficient -- AWS can still
  reject a `RunInstances` call for a specific AZ/instance-type combination
  due to capacity even with adequate quota. Not something Terraform plan
  can detect; only shows up at apply time. A Capacity Block or On-Demand
  Capacity Reservation is the usual mitigation if this becomes a recurring
  problem, not something provisioned here since it's a separate cost/
  commitment decision.
- **KV-cache math** above is a from-first-principles model (using this
  model's real `config.json` architecture, not a generic transformer
  formula) but is still an estimate of vLLM's *actual* runtime memory
  layout, which also includes activation memory, CUDA graph buffers, and
  vLLM's own reserved-fraction accounting (`gpu_memory_utilization`, default
  90%). Cross-check against vLLM's own startup log (`Maximum concurrency
  for X tokens per request: Y reqs`) once Phase 2 has the container running
  on real hardware -- that log line is vLLM's own memory calculator, and is
  the authoritative number this estimate should be checked against.
- **No load balancer / no separate CPU-only instance** is provisioned in
  this phase for the agent/loadgen/monitoring components (Phases 3/4) --
  the security group already opens the ports they'll need (scoped to the
  VPC CIDR), but where those components actually run (colocated on GPU
  nodes vs. a dedicated instance) is left to those phases.
- **Region**: everything above was verified in `us-east-1` specifically.
  Pricing, quota, EFA support, and instance-type-to-AZ offerings all vary
  by region -- re-verify before switching `aws_region`.

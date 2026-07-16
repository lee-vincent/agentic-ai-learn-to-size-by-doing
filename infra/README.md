# infra/ -- GPU Sizing Lab: networking, one GPU instance, IAM/secrets

Terraform for a **single-node, single-GPU** AWS lab sized against Qwen3.6-27B's
actual VRAM footprint at FP8 (and, as a knob, INT4). This is a deliberately
tightened scope -- see `SPEC.md`'s "Scope history" section for what an earlier
multi-node, multi-model draft looked like and why it was cut. **This module
only ever runs `terraform init`/`validate`/`plan`. Nothing here runs `apply`
or `destroy` -- that is a deliberate, separate, human-gated step.**

```
infra/
├── main.tf, variables.tf, outputs.tf, versions.tf   # root module: wires everything together
├── terraform.tfvars.example                          # documented knobs, copy to terraform.tfvars
├── scripts/check_gpu_quota.sh                        # vCPU quota check/request helper (family-agnostic)
└── modules/
    ├── networking/   # VPC, single subnet (auto-picks an AZ that offers the GPU instance
    │                 # type), IGW, one security group (internal-only vLLM/Prometheus/
    │                 # Grafana/Node-Exporter/DCGM ports, opt-in SSH)
    ├── iam/          # GPU-instance IAM role/instance profile + the HF_TOKEN SSM parameter slot
    └── compute/      # The single GPU instance (DLAMI) + its Elastic IP
```

## What changed vs. the earlier multi-node design

The previous version of this stack (still visible in git history) provisioned
a 2-node `p5.48xlarge` cluster with FSx for Lustre, EFA-typed network
interfaces, a cluster placement group, and per-node EIPs -- built for a
three-model, multi-parallelism lineup that turned out to be over-ambitious for
the immediate goal and kept hitting real EC2 capacity/quota friction. This
version:

- Provisions exactly **one** `aws_instance` (`modules/compute`) -- no
  `gpu_node_count`, no `count` on the instance resource, no cluster placement
  group, no EFA-typed network interface (there is no per-node ENI resource at
  all anymore -- the EIP attaches straight to the instance).
- Drops **FSx for Lustre and its S3 data-repository association entirely**
  (`modules/storage` no longer exists). Qwen3.6-27B's FP8 checkpoint is ~29
  GiB; there's no cross-node sharing problem to solve with a shared
  filesystem when there's only one node, so it downloads straight from the
  HuggingFace Hub onto the root EBS volume instead. See "Storage choice"
  below for the full reasoning, including why the S3 staging bucket was
  dropped too.
- Drops the FSx security group and its Lustre-protocol ingress rules, and the
  cluster security group's EFA/NCCL/Ray self-referencing all-traffic rule
  (there's no second node to talk to). The remaining security group keeps
  the same internal-only-by-default posture, just without Ray's ports
  (6379/8265), since there's no Ray cluster.
- Drops the `examples/g6e-multinode.tfvars` alternate profile -- that
  profile's whole purpose was quota-constrained multi-node bring-up while a
  P-family quota increase was pending; this design has no P-family default
  and no multi-node case to fall back from.
- `root_volume_size_gb` default is unchanged at 300 GiB, but its purpose
  changed: previously "OS + containers + vLLM/Ray installs only" (weights on
  FSx); now it also holds the model checkpoint(s) directly.
- IAM (`modules/iam`) keeps SSM Session Manager access, read-only ECR, and the
  HF_TOKEN SSM SecureString slot + its dedicated KMS key, unchanged in shape.
  The scoped S3 weights-bucket read policy is gone, since there's no bucket.

## Instance choice: `g6e.2xlarge` (1x L40S)

Verified live against AWS on **2026-07-16** (region `us-east-1`), not
recalled from memory:

| Fact | Value | Source |
|---|---|---|
| GPU | 1x NVIDIA L40S, 45,776 MiB = **44.7 GiB** VRAM | `aws ec2 describe-instance-types --instance-types g6e.2xlarge` |
| vCPUs | 8 | same call |
| RAM | 65,536 MiB = 64 GiB | same call |
| EFA support | `EfaSupported: false` (not used in this design -- single instance, no cross-node traffic) | same call |
| On-demand price | **$2.24208/hr** (Linux, `us-east-1`, shared tenancy) | AWS Price List API (`aws pricing get-products --service-code AmazonEC2 ...`), matches SPEC.md's `$2.24/hr` figure |
| Availability | Offered in `us-east-1a`, `us-east-1b`, `us-east-1c`, `us-east-1d` | `aws ec2 describe-instance-type-offerings` |

Why `g6e.2xlarge` over the alternatives SPEC.md flags:

- **`g6e.xlarge`** (4 vCPU / 32 GiB RAM, same 1x L40S GPU) is cheaper but
  risky: loading a 27B-parameter checkpoint plus request/vision
  preprocessing on 4 vCPUs / 32 GiB RAM leaves little headroom, per SPEC.md.
- **`g6e.4xlarge`** (16 vCPU / 128 GiB RAM, same 1x L40S GPU) gives more
  headroom if the default turns out to be tight, at a higher hourly rate. A
  one-line `gpu_instance_type` change; no other resource needs to change,
  since this module derives vCPU/GPU facts live via `aws_ec2_instance_type`
  rather than hardcoding them.
- No P-family or other 8-GPU node is considered here at all -- this is a
  single-GPU lab by design (see `SPEC.md`'s "Scope history").

`gpu_instance_type` is a variable -- switching between the above is a
one-line change (re-verify pricing/quota/availability, since all three
drift over time).

## VRAM footprint of Qwen3.6-27B -- verified, not estimated

SPEC.md flags its own back-of-envelope figures (BF16 ≈ 52 GiB, FP8 ≈ 29 GiB,
INT4/AWQ ≈ 14-15 GiB) as something to verify rather than trust outright.
Verified directly:

| Checkpoint | Fits `g6e.2xlarge`'s 44.7 GiB? | Headroom for KV cache/activations |
|---|---|---|
| BF16 (~52 GiB) | **No** -- exceeds the L40S's 44.7 GiB VRAM | n/a |
| **FP8 (~29 GiB, default)** | **Yes** | ~15 GiB |
| INT4/AWQ (~14-15 GiB) | **Yes**, most headroom | ~29-30 GiB |

This is exactly SPEC.md's framing: **FP8 is the default operating point;
BF16 is not an option on this single L40S.** `serving-builder` (Phase 2) owns
confirming the exact checkpoint size against the current Qwen3.6-27B model
card and vLLM's own startup-log memory accounting (`gpu_memory_utilization`,
default 90%) once the container is actually running -- that log line is
vLLM's own memory calculator and the authoritative number, not this
Terraform module's job to compute. This module's job is just to make sure
the instance has enough VRAM and disk headroom for that to be possible,
which it does at FP8 with room to spare.

Also worth flagging for `serving-builder` per SPEC.md: this is a
vision-language model, so the checkpoint includes a vision tower -- a modest
addition to the footprint even when this lab drives it with text/agentic
traffic only, already accounted for in the ~29 GiB FP8 figure above (verify
against the actual HF repo's `.safetensors` sizes before treating it as
exact).

## Storage choice: root EBS only, no FSx, no S3 staging bucket

The prior multi-node design used FSx for Lustre (so N nodes could share one
copy of a multi-hundred-GB checkpoint) plus an S3 staging bucket with a data
repository association (so a human/script could `aws s3 sync` once and let
FSx lazily hydrate from it). Neither problem exists here:

- **No FSx**: FSx for Lustre exists to solve *shared* access across multiple
  compute nodes. With exactly one instance, "shared" storage across nodes is
  moot -- a plain EBS volume is strictly simpler, has no separate hourly
  storage-capacity commitment, and (being `gp3`) is already fast enough for
  loading a ~29 GiB checkpoint once at instance boot/container start.
- **No S3 staging bucket**: the earlier bucket's entire purpose was to let
  FSx hydrate from S3 lazily instead of every node re-downloading from
  HuggingFace independently. With one node, "avoid every node
  re-downloading" isn't a problem to solve -- the instance just downloads
  the FP8 checkpoint straight from the HuggingFace Hub to its own EBS volume
  once. This also means one fewer IAM policy (no scoped S3 read) and one
  fewer resource whose lifecycle needs managing.
- **Root EBS, not a separate data volume**: SPEC.md allows either "a
  generously-sized EBS root" or "a dedicated EBS data volume." This module
  uses the former -- a second volume would need its own device-name mapping,
  a filesystem creation step, and a mount step in `user_data`, none of which
  buys anything for a single, short-lived lab instance where the OS, the
  vLLM container image, and the model checkpoint can all comfortably share
  one 300 GiB `gp3` volume (see the sizing table below). If this instance's
  role ever changes to something longer-lived where root-volume lifecycle
  (e.g. AMI-based replacement) needs to be decoupled from data lifecycle,
  revisit this -- but that's not this lab's shape.
- **`root_volume_size_gb` default: 300 GiB gp3, encrypted.** Verified gp3
  pricing (AWS Price List API, `us-east-1`, 2026-07-16): **$0.08/GB-month**
  for General Purpose (gp3) storage, no separate IOPS/throughput charge at
  the baseline 3,000 IOPS / 125 MiB/s (this workload doesn't need to
  provision above baseline). 300 GiB comfortably holds:
  - Ubuntu 22.04 + NVIDIA driver + Docker + NVIDIA Container Toolkit (DLAMI
    base, tens of GiB)
  - The vLLM container image
  - The Qwen3.6-27B FP8 checkpoint (~29 GiB) -- and there's room left to also
    keep the INT4/AWQ checkpoint (~14-15 GiB) cached alongside it for the
    precision-knob sweep in SPEC.md, without needing to re-download between
    runs.

## HF_TOKEN secret slot

Unchanged in shape from the prior design. The Qwen lineup in SPEC.md is
entirely Apache-2.0 and ungated, so this may never actually be used -- but
the slot is provisioned per SPEC.md's explicit requirement ("an optional SSM
SecureString slot for an `HF_TOKEN`").

- **SSM Parameter Store `SecureString`**, not Secrets Manager (no
  per-parameter monthly fee vs. Secrets Manager's ~$0.40/secret/month,
  worthwhile for a value that may sit unused for the project's entire life).
- **Dedicated KMS CMK**, so IAM can scope `kms:Decrypt` to exactly one key.
- **Terraform creates the parameter with a placeholder value and never
  touches it again** (`lifecycle { ignore_changes = [value] }` in
  `modules/iam/main.tf`) -- no real token is ever written to any
  `.tf`/`.tfvars`/state file.
- **Injection command** (run by a human, out-of-band, after `apply` -- never
  by Terraform):
  ```
  aws ssm put-parameter \
    --name "/gpu-sizing-lab/hf-token" \
    --type SecureString \
    --key-id "alias/gpu-sizing-lab-hf-token" \
    --value "<real token>" \
    --overwrite \
    --region us-east-1
  ```
  Also emitted as the `hf_token_injection_command` Terraform output.
- **IAM scope**: the GPU-instance role gets exactly `ssm:GetParameter` on
  this one parameter's ARN and `kms:Decrypt`/`kms:DescribeKey` on this one
  CMK's ARN -- nothing broader. It separately gets
  `AmazonSSMManagedInstanceCore` (Session Manager access, since SSH is closed
  by default) and `AmazonEC2ContainerRegistryReadOnly` (read-only, in case
  the vLLM image is ever hosted in ECR rather than built locally -- harmless
  to keep provisioned either way).

## GPU instance vCPU quota -- checked, and it is sufficient

Checked live against this AWS account (`733937259882`, `us-east-1`, root
credentials, checked **2026-07-16**) via `aws service-quotas
get-service-quota`:

| Quota | Current value | Needed for this plan (1x `g6e.2xlarge`) | Sufficient? |
|---|---|---|---|
| `L-DB2E81BA` "Running On-Demand G and VT instances" (vCPUs) | **384** | 8 | **Yes** -- 376 vCPUs of headroom |
| `L-417A185B` "Running On-Demand P instances" (vCPUs) | 384 | n/a (default instance type is G-family) | n/a |

No pending quota-increase requests exist on this account for either quota
(`aws service-quotas list-requested-service-quota-change-history-by-quota`
returned an empty list). **No quota increase is needed to apply this plan as
configured.** This is worth calling out explicitly because most AWS accounts
start at (or near) 0 for the G/P instance families -- this account happens to
already have ample G&VT headroom, but that won't be true for every account
running this lab, so the check below is still part of the documented
pre-`apply` workflow.

Three ways to check/act on quota, in increasing order of automation:

1. **Terraform output** (read-only, computed every `plan`/`apply`, from
   `data.aws_servicequotas_service_quota` in `main.tf`; automatically
   switches between the G&VT and P pools based on `gpu_instance_type`):
   ```
   terraform output quota_check
   ```
2. **`infra/scripts/check_gpu_quota.sh`** -- read-only by default, only
   submits a request when you explicitly pass `--request <value>`. Derives
   vCPUs and the applicable quota code live from `--instance-type` (via
   `aws ec2 describe-instance-types`) rather than a hardcoded table:
   ```
   ./scripts/check_gpu_quota.sh                              # default: 1x g6e.2xlarge, checks G&VT quota
   ./scripts/check_gpu_quota.sh --instance-type g6e.4xlarge   # a bigger single-instance option
   ./scripts/check_gpu_quota.sh --request 8                  # request exactly enough for 1x g6e.2xlarge
   ```
3. **Raw AWS CLI**, if you'd rather not use the script:
   ```
   aws service-quotas get-service-quota --service-code ec2 --quota-code L-DB2E81BA
   aws service-quotas request-service-quota-increase --service-code ec2 --quota-code L-DB2E81BA --desired-value <value>
   ```

Terraform deliberately does **not** include the `aws_servicequotas_
service_quota` *resource* (which would file a real increase request as a
side effect of `terraform apply`). Requesting a quota increase is treated
here as its own explicit, human-initiated action, consistent with this
project's guardrail that cost/capacity-adjacent actions require explicit
confirmation.

## Cost estimate (for human review)

All rates verified live via the **AWS Price List API**, `us-east-1`,
**2026-07-16**:

| Item | Rate | Qty | Hourly cost |
|---|---|---|---|
| `g6e.2xlarge` on-demand | $2.24208/hr | 1 | $2.24208 |
| EBS `gp3` storage | $0.08/GB-month | 300 GiB / 730 hr | $0.0329 |
| Public IPv4 (EIP, in-use) | $0.005/hr | 1 | $0.005 |
| **Total** | | | **≈ $2.28/hr (≈ $54.72/day if left running)** |

Not included above (usage-based, effectively $0 until you actually make
requests): S3 request charges for the initial HuggingFace Hub download
(HF Hub itself is not an AWS service and isn't billed by AWS; standard
internet-egress data-transfer rates from AWS apply only to *outbound*
traffic from the instance, which is negligible here since downloads are
*inbound*). None of these change the order of magnitude; compute is >95% of
the hourly cost.

Scaling notes:
- Switching to `g6e.4xlarge` (16 vCPU/128 GiB, same 1x L40S GPU): re-verify
  pricing before switching (roughly ~2x the `g6e.2xlarge` compute rate, based
  on the vCPU/RAM ratio, but confirm live -- don't assume linear scaling
  holds for every EC2 family/generation).
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
1. Confirm the vCPU quota (see above) -- `apply` will fail without it if
   your account doesn't already have it (this account, `733937259882`, does).
2. Review `terraform plan`'s resource count and the cost table above.
3. Decide whether you actually want the HF_TOKEN slot populated (see
   injection command above) -- it's fine to leave it as the placeholder
   indefinitely since the Qwen lineup is ungated.

## Assumptions and things not fully verified

- **DLAMI driver bundling**: `modules/compute` boots the instance from
  `/aws/service/deeplearning/ami/x86_64/base-oss-nvidia-driver-gpu-ubuntu-22.04/latest/ami-id`,
  whose description explicitly lists G6e support and ships the NVIDIA driver
  + Docker + NVIDIA Container Toolkit. Confirm this AMI variant's release
  notes before a real apply if there's any doubt.
- **G6e capacity**: quota is necessary but not sufficient -- AWS can still
  reject a `RunInstances` call for a specific AZ/instance-type combination
  due to capacity even with adequate quota. Not something `terraform plan`
  can detect; only shows up at apply time. L40S/G6e is a widely-available
  instance family (chosen partly for this reason, per SPEC.md), so this is a
  low-probability concern relative to the earlier P5-based design, but still
  worth knowing about.
- **No load balancer / no separate CPU-only instance** is provisioned for
  the agent/loadgen/monitoring components (Phases 3/4) -- the security group
  already opens the ports they'll need (scoped to the VPC CIDR), and
  everything is expected to run colocated on this one instance per SPEC.md
  ("Everything runs on the single node").
- **Region**: everything above was verified in `us-east-1` specifically.
  Pricing, quota, and instance-type-to-AZ offerings all vary by region --
  re-verify before switching `aws_region`.

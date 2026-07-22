# monitoring — Prometheus + Grafana + DCGM Exporter + Node Exporter

Phase 4 of the GPU Sizing Lab (see repo-root `SPEC.md` / `GOALS.md`). Scope for this stack: scrape
CPU/RAM, GPU/VRAM, and vLLM's own native inference metrics on the **single** `g6e.2xlarge` node that
already runs vLLM (`containers/vllm/`), and combine them into one Grafana dashboard per experiment
run. This is a separate Compose project from `containers/vllm/` -- it never touches that file, it
just scrapes the `/metrics` endpoint vLLM already publishes on the host.

Turnaround Time (TAT) is **not** in this dashboard on purpose: SPEC.md is explicit that TAT is
measured client-side by `agent-builder`'s agent (it spans the full round trip including agent
reasoning/tool steps), not inferred from Prometheus. `vllm:e2e_request_latency_seconds` exists as a
server-side proxy but is a different, narrower quantity than client-side TAT -- see "Known
uncertainties" below.

## What's here

| File | Purpose |
|---|---|
| `docker-compose.yml` | prometheus, grafana, node-exporter, dcgm-exporter -- one Compose project, all `network_mode: host` (see the comment block at the top of the file for why). |
| `prometheus/prometheus.yml` | Scrape jobs: `vllm` (localhost:8000), `node` (localhost:9100), `dcgm` (localhost:9400), `prometheus` (self, localhost:9090). |
| `grafana/provisioning/datasources/prometheus.yml` | Auto-provisions the Prometheus datasource (`uid: prometheus`, `http://localhost:9090`). |
| `grafana/provisioning/dashboards/dashboards.yml` | Dashboard provider config -- loads every `*.json` in this same folder. |
| `grafana/provisioning/dashboards/gpu-sizing-lab.json` | The one dashboard (`uid: gpu-sizing-lab`), hand-written, 3 panel rows: CPU/RAM, GPU/VRAM, Inference. |
| `.env.example` | Admin password + pinned image tag overrides. |

## Networking: `network_mode: host`

Every service in this Compose project runs with `network_mode: host`. This means each service
binds directly to the host's own network stack on its default port, and none of the services
declare a `ports:` mapping (that key is meaningless -- and produces a Compose runtime warning --
under host networking). Concretely, once this is up on the target host:

| Service | Host port | Bound by |
|---|---|---|
| Prometheus | `9090` | `--web.listen-address=0.0.0.0:9090` |
| Grafana | `3000` | `GF_SERVER_HTTP_PORT=3000` |
| Node Exporter | `9100` | `--web.listen-address=0.0.0.0:9100` |
| DCGM Exporter | `9400` | `DCGM_EXPORTER_LISTEN=:9400` (its default) |
| vLLM (`containers/vllm/`, not this project) | `8000` | already published by that compose file |

Because everything (including vLLM) ends up reachable at `localhost:<port>` from the host's own
network namespace, Prometheus's scrape targets in `prometheus/prometheus.yml` are all
`localhost:<port>` -- no bridge network, no `extra_hosts: host-gateway` plumbing needed. This
matches the deployment brief: the security group already restricts 9090/3000/9100/9400 to the VPC,
so binding to `0.0.0.0` here is intentional and safe -- external reachability is controlled at the
SG layer, not by this compose file.

## Deploy on the instance

Prerequisite: vLLM is already running (`containers/vllm/`, container `vllm-qwen3_6-27b`, port 8000)
and NVIDIA driver + Container Toolkit are already installed and working (per this host's brief).

```bash
cd /opt/monitoring-app/monitoring        # wherever this repo's monitoring/ lives on the host
cp .env.example .env                     # edit GF_SECURITY_ADMIN_PASSWORD if you want something
                                          # other than the lab default
docker compose up -d
docker compose ps
docker compose logs -f prometheus grafana dcgm-exporter node-exporter
```

Sanity-check GPU passthrough for dcgm-exporter before bringing the stack up, same pattern as
`containers/vllm/README.md`:

```bash
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

Tear down (does not touch `containers/vllm/`):

```bash
docker compose down          # add -v to also drop the prometheus_data/grafana_data volumes
```

## Operator access via SSM port-forward

The security group only opens 9090/3000/9100/9400 within the VPC, so reach them from an operator
workstation via SSM Session Manager port-forwarding (no SSH key, no public IP needed):

```bash
aws ssm start-session \
  --target <instance-id> \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["3000"],"localPortNumber":["3000"]}'
# repeat with portNumber 9090 (Prometheus) / 9100 (Node Exporter) / 9400 (DCGM Exporter) as needed,
# each on its own localPortNumber if you forward more than one at a time.
```

Then browse `http://localhost:3000` (Grafana) / `http://localhost:9090` (Prometheus) locally.

## Target list (what Prometheus should show as "up")

| Job | Target | Backed by |
|---|---|---|
| `vllm` | `localhost:8000` | `vllm-qwen3_6-27b` container's native `/metrics` (containers/vllm/) |
| `node` | `localhost:9100` | `node-exporter` service, this project |
| `dcgm` | `localhost:9400` | `dcgm-exporter` service, this project |
| `prometheus` | `localhost:9090` | Prometheus itself |

## How the `checker` can verify this (Phase 4 goal)

1. **Every target "up":**
   ```bash
   curl -s http://localhost:9090/api/v1/targets | python3 -m json.tool
   ```
   Confirm all four jobs (`vllm`, `node`, `dcgm`, `prometheus`) report `"health": "up"` for their
   one target each.

2. **Grafana is alive:**
   ```bash
   curl -s http://localhost:3000/api/health
   ```
   Expect `{"commit":"...","database":"ok","version":"..."}`.

3. **The dashboard loads (anonymous Viewer access is enabled specifically so this works without
   a login):**
   ```bash
   curl -s http://localhost:3000/api/dashboards/uid/gpu-sizing-lab | python3 -m json.tool
   ```
   Confirm the response's `dashboard.panels` array has 16 non-row panels (CPU/RAM: 3, GPU/VRAM: 5,
   Inference: 8) and every one has a non-empty `targets[].expr`.

4. **Panels render without "no data"/query errors once load is running:** the KV cache hit rate,
   TTFT, and ITL panels only show non-flat lines once `genai-perf` or the agent harness
   (`loadgen/`) is actually sending traffic to vLLM -- an idle vLLM will show `0`/flat lines, which
   is a correct empty state, not a broken panel. Re-check with load running before concluding a
   panel is actually broken.

## vLLM metric names used, and why (v0.25.1 -- verified against source, not assumed)

Per this task's instruction not to build a custom exporter for TTFT/KV-cache-hit-rate since vLLM
exposes them natively: every `vllm:*` series below was confirmed by fetching the actual pinned
source file for this build --
`https://raw.githubusercontent.com/vllm-project/vllm/v0.25.1/vllm/v1/metrics/loggers.py` -- and
cross-checking against the installed `prometheus_client` library's own `Counter` implementation
(`vllm/v1/metrics/loggers.py` uses `_counter_cls = Counter`, `_gauge_cls = Gauge`,
`_histogram_cls = Histogram` straight from `prometheus_client`, not a custom wrapper), rather than
trusted from memory or from `containers/vllm/README.md` alone (though that README's independent
read of the same source agrees).

**The one non-obvious detail: Counter names get `_total` appended at scrape time.**
`vllm/v1/metrics/loggers.py` declares these as, e.g., `Counter(name="vllm:prefix_cache_queries",
...)` -- no `_total` in the source literal. But `prometheus_client`'s `Counter` class
unconditionally appends `_total` to the name it actually exposes on the wire
(`prometheus_client/metrics.py`: `self._value = values.ValueClass(self._type, self._name,
self._name + '_total', ...)`, and `_build_full_name` explicitly strips a trailing `_total` from
the *source* name first specifically so it isn't doubled). This is standard, longstanding
`prometheus_client` behavior for every Counter, not something specific to this metric. So:

| SPEC.md metric | Source-literal name (`loggers.py`) | **Actual scraped series name** |
|---|---|---|
| KV cache hit rate -- queries | `vllm:prefix_cache_queries` (Counter) | **`vllm:prefix_cache_queries_total`** |
| KV cache hit rate -- hits | `vllm:prefix_cache_hits` (Counter) | **`vllm:prefix_cache_hits_total`** |
| Output token throughput | `vllm:generation_tokens` (Counter) | `vllm:generation_tokens_total` |
| Prompt token throughput | `vllm:prompt_tokens` (Counter) | `vllm:prompt_tokens_total` |
| Completed requests | `vllm:request_success` (Counter) | `vllm:request_success_total` |

Gauges and Histograms are **not** affected by this suffixing (only Counters are), so these are
used exactly as declared in source:

| SPEC.md metric | Series name | Type |
|---|---|---|
| TTFT | `vllm:time_to_first_token_seconds` | Histogram (`_bucket`/`_sum`/`_count`) |
| ITL | `vllm:inter_token_latency_seconds` | Histogram |
| Latency per output token | `vllm:request_time_per_output_token_seconds` | Histogram -- literally "per request: total generation time / total output tokens", matching SPEC.md's definition of this metric exactly (vLLM computes it, we don't derive it) |
| KV cache usage | `vllm:kv_cache_usage_perc` | Gauge, **0-1 fraction** despite the `_perc` name (doc string: "1 means 100 percent usage") -- dashboard uses Grafana's `percentunit` display unit rather than multiplying by 100 in PromQL |
| Running/waiting requests | `vllm:num_requests_running` / `vllm:num_requests_waiting` | Gauge |

**KV cache hit rate**, as used in `gpu-sizing-lab.json`:
```promql
sum(rate(vllm:prefix_cache_hits_total[5m])) / sum(rate(vllm:prefix_cache_queries_total[5m]))
```
This is vLLM's native **prefix cache** hit rate (hits/queries are counted in units of tokens, per
the metric's own `documentation=` string in source), which is the mechanism SPEC.md's "KV cache hit
rate" metric refers to for this build (prefix caching is confirmed enabled per this task's brief).
vLLM 0.25.1 also exposes a parallel `vllm:external_prefix_cache_queries` /
`vllm:external_prefix_cache_hits` pair for KV-connector cross-instance cache sharing -- not
applicable here (single node, no KV connector), so not used.

## Known uncertainties -- things the `checker` should scrutinize on the live instance

- **Everything above is verified against source, not against a live scrape.** This container was
  built with no GPU and without the vLLM server actually running, so no query in
  `gpu-sizing-lab.json` has been confirmed against a real `/metrics` response body. The `checker`
  (or a human) should `curl http://localhost:8000/metrics | grep vllm:prefix_cache` on the actual
  host once vLLM is serving real traffic and confirm the series names match exactly (including
  that they carry the `engine="0"`/`model_name="..."` labels this multi-engine-aware logger class
  attaches -- the PromQL here sums across labels with `sum(...)`, which is correct for the
  single-engine case but worth a glance).
- **`vllm:kv_cache_usage_perc` may report `NaN`/absent briefly at startup** before the KV cache
  is initialized -- if the panel shows "No data" immediately after `docker compose up`, wait for
  vLLM to finish engine init rather than assume the panel is broken.
- **DCGM Exporter image tag** (`nvcr.io/nvidia/k8s/dcgm-exporter:4.5.2-4.8.1-ubuntu22.04`) was
  confirmed to actually exist on `nvcr.io` (queried the registry's tag-list API directly) and to be
  a recent, non-RC build, but was **not** pulled/run locally (no GPU on this dev box, and this
  task's brief explicitly says not to try). Confirm on the real L40S host that
  `DCGM_FI_DEV_GPU_UTIL` / `DCGM_FI_DEV_FB_USED` / `DCGM_FI_DEV_FB_FREE` /
  `DCGM_FI_DEV_POWER_USAGE` / `DCGM_FI_DEV_SM_CLOCK` are the field names this exact tag's default
  `counters.csv` actually exposes (these five have been stable DCGM field names for years across
  many exporter versions, so this is low-risk, but "verify, don't assume" per SPEC.md).
- **`cap_add: SYS_ADMIN`** is included for dcgm-exporter per NVIDIA's own reference deployment
  examples (needed for some NVML/profiling counters); if the real host's kernel/driver combo
  doesn't need it, it's harmless to leave in, but if dcgm-exporter fails to start, this is one of
  the first things to check the container logs about.
- **`vllm:e2e_request_latency_seconds`** exists in source (server-side request latency) but is
  deliberately **not** in this dashboard -- SPEC.md/CLAUDE.md are explicit that TAT is a
  client-side, agent-layer measurement (spans agent reasoning/tool steps, not just token
  generation), so conflating it with this narrower server-side histogram would be wrong. Anyone
  wanting a server-side latency sanity-check alongside `agent-builder`'s real TAT log can add it
  ad hoc in Grafana's Explore view; it's not wired into the provisioned dashboard.
- **Grafana anonymous Viewer access** (`GF_AUTH_ANONYMOUS_ENABLED=true`) is scoped to the `Main
  Org.` default org at the `Viewer` role only -- read-only, no edit/admin capability. This is
  intentional per this task's brief ("enable anonymous Viewer access so the checker can hit
  dashboards unauthenticated") but is a deliberate security loosening worth knowing about if this
  ever moves beyond a VPC-internal lab.
- **`.env.example` naming vs. repo `.gitignore`:** the repo-root `.gitignore` has a blanket
  `.env.*` rule (meant to keep real secrets out), which also matches this file's literal name.
  `git add -f monitoring/.env.example` (or a small `.gitignore` exception) will be needed whenever
  this actually gets committed -- flagging it here since fixing `.gitignore` itself is outside
  `monitoring/`'s scope for this build.

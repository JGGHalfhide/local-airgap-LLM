# Load test and energy per token

Throughput, latency and energy of the inference endpoint at concurrency
1, 2, 4 and 8, on the build cluster (build-node-01: 8 vCPU / 16 GiB on the
Ryzen 7 8845HS, full platform stack running, airgap VM off). Script:
[scripts/load-test.py](../scripts/load-test.py). Raw output:
[scans/week6-load-test.txt](scans/week6-load-test.txt) and
[.json](scans/week6-load-test.json).

## Method

- **Path:** through the KEDA interceptor, as in-cluster clients do
  (`kubectl port-forward` to the interceptor from the host, `Host:
  llm-api.llm.svc`). The extra hop costs well under a millisecond per
  streamed chunk.
- **Requests:** 16 per concurrency level, streamed chat completions,
  temperature 0, thinking off (`chat_template_kwargs`), `max_tokens` 128
  (most replies used all 128). Prompt: a 213-token system context plus one
  of eight operator questions. Each prompt starts with a unique request id,
  so no request reuses another's cached prefix: every one pays full prompt
  processing.
- **Measured client side:** TTFT (first content token in the stream),
  end-to-end latency, completion tokens (from the stream's `usage` chunk),
  generation tokens/s per request (tokens after the first / time after the
  first). A request counts as successful only if its stream ends with the
  usage chunk and `[DONE]`.
- **Energy:** RAPL CPU package counter on the host
  (`/sys/class/powercap/intel-rapl:0/energy_uj`), read before and after each
  level; 60 s idle baseline with the server warm and no requests. A warm-up
  request first, so no cold start is in the numbers
  ([scaling.md](scaling.md) has those).

## Results (11/23, build cluster)

| Concurrency | Errors | Output tok/s (all requests) | Gen tok/s per request | TTFT p50 | TTFT p95 | Latency p50 | Latency p95 | CPU package W | J / output token |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 0 / 16 | 11.2 | 14.3 | 1.9 s | 2.1 s | 8.0 s | 10.9 s | 51.3 | 4.58 |
| 2 | 0 / 16 | 12.7 | 8.1 | 2.3 s | 4.0 s | 13.3 s | 20.6 s | 49.8 | 3.92 |
| 4 | 0 / 16 | 18.2 | 6.0 | 2.6 s | 8.0 s | 20.5 s | 30.0 s | 48.9 | 2.69 |
| 8 | 0 / 16 | 18.7 | 6.0 | 22.0 s | 32.4 s | 36.1 s | 56.2 s | 48.7 | 2.61 |

Idle (server warm, cluster running): 11.4 W CPU package.

- **One stream: 14.3 tokens/s, first token in 1.9 s** for a 213-token
  prompt (prompt processing ~110 tokens/s). The aggregate at concurrency 1
  (11.2) is lower because it includes the prompt time of each request.
- **Throughput tops out at 4 concurrent requests: ~18 tokens/s**, 1.6x a
  single stream. Generation is memory-bandwidth bound
  ([benchmarks/README.md](../benchmarks/README.md)), so batching four
  sequences shares the weight reads but each stream slows to ~6 tokens/s.
- **Above 4, requests queue.** The server has 4 slots; at concurrency 8
  throughput is flat and TTFT jumps to 22 s p50 (waiting for a free slot).
  The ScaledObject's target of 4 per replica matches this, but the node
  fits only one replica, so 4 concurrent requests is the useful ceiling on
  this hardware.
- **No errors and no restarts** at any level after the fix below.
- **Power is flat at ~49-51 W** whatever the concurrency, so energy per
  token falls as batching rises: 4.6 J at 1, 2.7 J at 4.

## Energy per million tokens

From the table: kWh per million output tokens = J per token / 3.6.

| Concurrency | kWh / M output tokens (total CPU package) | kWh / M output tokens (above idle) |
|---|---|---|
| 1 | 1.27 | 0.99 |
| 2 | 1.09 | 0.84 |
| 4 | 0.75 | 0.57 |
| 8 | 0.73 | 0.56 |

- **About 0.75 kWh per million output tokens at the useful concurrency
  (4)**, 1.3 kWh for a single stream. Multiply by your tariff for a cost;
  no price is assumed here.
- "Total" is all CPU package energy during the run, including what the
  cluster draws idle; "above idle" subtracts the 11.4 W baseline, the
  marginal cost of serving.
- **Lower bound for the wall.** RAPL counts the CPU package (cores, iGPU,
  memory controller), not DRAM, SSD, fans or power-supply losses. A wall
  meter would read higher; none was used.
- Output tokens only, with thinking off. With Qwen3.5's default thinking
  on, the cost per token is the same but an answer takes many more tokens
  (134 for a one-word answer in the smoke test).
- Short prompts (213 tokens). Longer prompts add prompt processing time
  and energy per request, not counted per output token here.

## Finding: OOMKilled under load (fixed)

The first run lost requests: streams ended without `[DONE]`, and TTFT p95
reached 49 s at concurrency 2. The pod had restarted four times,
`OOMKilled` (exit 137) at its 6 GiB limit, with anonymous memory at
5.8 GiB. The Week 1 memory figure (4.6 GiB) came from a single request.

Cause: llama-server's RAM prompt cache (`--cache-ram`, default 8192 MiB,
[llama.cpp#16391](https://github.com/ggml-org/llama.cpp/pull/16391)) saves
the state of every prompt that leaves a slot. With 4 slots and unique
prompts it grows until it hits its cap, which is above the pod limit.
Reproduced on the host with the same flags and load (12 requests at
concurrency 4):

| `--cache-ram` | Peak RSS |
|---|---|
| default (8192 MiB) | 6482 MiB, still growing |
| 1024 | 6006 MiB |
| 0 (off) | 5056 MiB |

Fix (c362a5a): `--cache-ram 0` in `deploy/llm/llama-server.yaml`. In the
run above the container's anonymous memory stayed at 5036 MiB (cgroup peak
5983 MiB including 677 MiB of reclaimable file cache), 0 restarts. A slot
still reuses its own KV prefix when the next prompt in it shares one
(`cache_prompt`, on by default); what is lost is restoring an evicted
conversation from RAM. Phase 3 agents with a long shared system prompt may
want a small cache back, with the limit raised to match.

## Not measured

- The airgapped cluster under load (same VM size and images, measured
  only with single requests: [airgap-runbook.md](airgap-runbook.md)).
- Long prompts (only 213 tokens here) and long generations.
- Wall power.

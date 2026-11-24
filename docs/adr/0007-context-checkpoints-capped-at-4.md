# 0007. Context checkpoints capped at 4 per slot

Status: accepted (after v1.1, 11/24)

## Context

Phase 3 (local-agents) runs agents whose tool loops send many requests per
conversation: each turn resends the conversation plus the newest tool
output, 1k-7k-token prompts, and the slot reuses the cached prefix. The
first such workload (local-agents' ADR 0002 smoke test, 11/24) got
llama-server `OOMKilled` at its 6 GiB limit with `--cache-ram 0` already
set (the Week 6 fix, docs/load-test.md), so the RAM prompt cache was not
the cause.

Measured on the build cluster by replaying the 27 recorded requests
(`scripts/agent-mem-replay.py`, pod cgroup sampled every 2 s; full table and
raw data in [scans/agent-mem-replay.txt](../scans/agent-mem-replay.txt)):

| Run | Setting | Limit | OOM | Max anon | Max cgroup |
|---|---|---|---|---|---|
| E1 | 4 slots (auto, unified 32k KV), defaults | 6 GiB | 1 restart at request 16 | 6037 MiB | 6143 MiB |
| E2 | same, every request on slot 0 (`id_slot`) | 6 GiB | none | 5787 MiB | 6143 MiB |
| E3 | 4 slots, defaults | 7.5 GiB | none, still rising at the end | 6791 MiB | 7048 MiB |
| E4 | `--parallel 2` (KV not unified: 16k per slot) | 6 GiB | none, still rising at the end | 5877 MiB | 5890 MiB |
| E5 | `--parallel 2`, two one-shot 14,970-token prompts | 6 GiB | none | 4745 MiB | 4756 MiB |
| E6 | 4 slots, `--ctx-checkpoints 4` | 6 GiB | none, flat from request 16 | 5286 MiB | 5298 MiB |

- With the defaults, anonymous memory grew 50-250 MiB with every turn that
  extended a cached conversation, and was freed only when a slot took a
  different conversation (E2 vs E1). It did not follow the number of
  tokens held: two slots holding 15k tokens each cost about 100 MiB (E5).
- A higher limit (E3) or fewer slots (E4) only delayed it: memory was still
  rising at the last request. Two slots also turn the unified KV cache off
  (16k per slot) and halve concurrency.
- `--ctx-checkpoints` (default 32 per slot) bounds what each slot keeps for
  resuming a conversation; Qwen3.5 is a hybrid (recurrent + attention)
  model, for which llama.cpp keeps such checkpoints. Capped at 4 (E6),
  memory rose only when a slot was first used and stayed between 5185 and
  5286 MiB for the rest of the run. The server's log does not report
  checkpoints at the default level; the evidence is the memory curve.
- Cost: total replay time 726 s vs 722 s; one short request took 9.4 s
  instead of 5.2 s (its prefix could not be resumed). Prompt processing
  ~115 tok/s as before.

## Decision

`--ctx-checkpoints 4` in `deploy/llm/llama-server.yaml`. Everything else
unchanged: 4 slots, unified 32k KV cache, `--cache-ram 0`, memory request
5 GiB, limit 6 GiB.

Revisit (new ADR) if: a workload with more concurrent conversations or
longer ones than this replay shows memory above about 5.5 GiB; a llama.cpp
upgrade changes checkpoint behaviour (rerun the replay); or a second model
shares the node.

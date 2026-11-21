# Benchmarks

Week 1 model selection. Decision: [ADR 0002](../docs/adr/0002-qwen3.5-4b-q4km-model.md).

## Reproduce

```bash
scripts/build-llama-bench.sh                        # llama.cpp v1.0.0, CPU, -march=native
scripts/fetch-models.sh                             # candidates.tsv -> models/, SHA-256 checked
scripts/bench.py --label host                       # llama-bench: thread sweep + 4k-context pass
scripts/toolcall-check.py --label host              # 5 tool-calling / JSON cases via llama-server
scripts/server-mem.sh models/Qwen3.5-4B-Q4_K_M.gguf # llama-server peak memory by setting
```

Raw output: `results/<label>/` (llama-bench JSON with full metadata,
tool-call responses, memory table).

## Host, 11/21

Ryzen 7 8845HS (8C/16T, AVX-512), 28 GiB, Arch Linux, both VMs off.
llama.cpp v1.0.0 (`b1b770d9`). llama-bench, 3 repetitions, standard
deviation under 2%. Tokens/s.

| Model | Threads | pp512 | tg128 | pp512 @4k ctx | tg128 @4k ctx | Load+1 tok | Peak RSS |
|---|---|---|---|---|---|---|---|
| Granite 4.2 3B Q4_K_M | 4 / 8 / 16 | 113 / 201 / 198 | 25.4 / 25.9 / 25.6 | 118 | 15.0 | 0.5 s | 3.7 GiB |
| Qwen3.5 4B Q4_K_M | 4 / 8 / 16 | 80 / 145 / 140 | 16.5 / 16.6 / 16.4 | 124 | 15.0 | 1.4 s | 4.6 GiB |
| Qwen3.5 4B Q8_0 | 4 / 8 / 16 | 68 / 123 / 116 | 11.6 / 11.7 / 11.2 | 107 | 11.0 | 1.3 s | 4.4 GiB |
| Granite 4.2 8B Q4_K_M | 4 / 8 / 16 | 46 / 83 / 80 | 10.8 / 11.0 / 10.7 | 56 | 7.7 | 3.1 s | 8.8 GiB |
| Ministral 3 8B Q4_K_M | 4 / 8 / 16 | 49 / 88 / 86 | 11.3 / 11.5 / 11.3 | 62 | 8.2 | 3.2 s | 8.0 GiB |

The 4k-context columns are at 8 threads. Load+1 tok is with a warm page
cache. Peak RSS here is llama-bench with mmap, so it counts the mapped file
as well as the repacked weights; see the server table below.

### Tool calling (scripts/toolcall-check.py)

Five cases at temperature 0, thinking disabled: one tool with correct
arguments, the right tool out of three, no tool when none is needed,
JSON-schema output, a final answer from a tool result. **All five models
passed 5/5**; slowest case per model: Granite 3B 4.7 s, Qwen3.5 4B Q4 6.7 s,
Qwen3.5 4B Q8 8.6 s, Granite 8B 11.0 s, Ministral 8B 11.4 s. These cases
are easy; they check the plumbing (chat template, tool-call parsing,
grammar-constrained JSON), not reasoning quality.

### llama-server memory, Qwen3.5 4B Q4_K_M

After one ~3k-token request, 8 threads, 4 slots:

| Setting | Peak | Anonymous | File-backed |
|---|---|---|---|
| mmap (default), ctx 8k | 5.1 GiB | 2.2 GiB | 2.8 GiB |
| `-lm none`, ctx 8k | 3.9 GiB | 3.9 GiB | 0 |
| `-lm none -nr` (no repack), ctx 8k | 3.6 GiB | 3.5 GiB | 0 |
| `-lm none`, ctx 32k | 4.6 GiB | 4.6 GiB | 0 |
| `-lm none`, ctx 32k, 1 slot | 4.5 GiB | 4.5 GiB | 0 |

With mmap the file stays resident next to the CPU-repacked copy of the
weights, so `--load-mode none` (v1.0's replacement for `--no-mmap`) saves
about 1.2 GiB. Repacking costs about 0.3 GiB and is kept for speed.
Qwen3.5 uses few full-attention layers, so 32k context adds only about
0.7 GiB.

## Findings

- 8 threads (the physical cores) is best; the 16 SMT threads add nothing.
- Generation is memory-bandwidth bound: 4 threads is within 3% of 8.
  Prompt processing scales with cores.
- Qwen3.5 4B keeps about 90% of its speed at 4k context; the others keep
  60-70%.

## Still to run

- The same benchmark inside build-node-01 (VM overhead, thread count for
  the pod).

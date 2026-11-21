# 0001. llama.cpp server as the inference runtime

Status: accepted (Week 1, 11/21)

## Context

Inference runs on CPU only (Ryzen 7 8845HS, 8 cores / 16 threads, AVX-512),
inside RKE2 pods under platform-lab's restricted Pod Security and Kyverno
policies, and has to work with no internet on the airgapped cluster. Phase 3
(agents on Kubernetes) will call it as an in-cluster client, so it needs an
OpenAI-compatible API with tool calling and structured output.

Options considered:

- **vLLM**: built around GPU serving and unquantized or GPU-oriented
  quantization formats; its CPU path is secondary, and the image is large.
- **Ollama**: wraps llama.cpp, but pulls models from its own registry and
  model format at runtime, which works against delivering the model as a
  signed OCI artifact through Zot and Zarf.
- **llama.cpp server** (`llama-server`): CPU-first (AVX-512 kernels, weight
  repacking for the CPU), runs GGUF files directly from a path, one static
  binary, runs as non-root, exposes an OpenAI-compatible API (chat
  completions, tool calls via each model's chat template with `--jinja`,
  JSON-schema-constrained output) and Prometheus metrics.

Week 1 measurements with llama.cpp v1.0.0 (benchmarks/README.md): all five
candidate models passed the five tool-calling and structured-output cases;
loading with `--load-mode none` (no mmap) cut peak memory for the chosen
model from 5.1 to 3.9 GiB at 8k context, because the memory-mapped file
otherwise stays resident alongside the CPU-repacked copy of the weights.

## Decision

Use `llama-server` from llama.cpp, pinned to release v1.0.0 (commit
`b1b770d90d94cad9e9ea5b993bdbc383a522fb28`), built for CPU only. Run it with
`--jinja`, `--load-mode none`, weight repacking on, and a thread count set
from the in-VM benchmark (8 on the bare host). The runtime image goes
through platform-lab's `make image` flow (SBOM, Trivy gate, cosign) in
Week 2.

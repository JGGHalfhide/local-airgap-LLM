# local-airgap-LLM

An openly licensed, quantized LLM served on CPU inside the
[platform-lab](https://github.com/JGGHalfhide/platform-lab) Kubernetes
platform, delivered through the same supply chain as every image (signed,
verified at admission), scaled and observed, and deployed to an airgapped
cluster with no internet.

[![ci](https://github.com/JGGHalfhide/local-airgap-LLM/actions/workflows/ci.yml/badge.svg)](https://github.com/JGGHalfhide/local-airgap-LLM/actions/workflows/ci.yml)

**Status:** Week 1 of 6 (model choice by benchmark). See [ROADMAP.md](ROADMAP.md).

## How it fits with platform-lab

- **platform-lab** provides the clusters: VMs (Terraform), OS hardening, RKE2,
  Argo CD, Zot, Kyverno, cosign, monitoring, Velero, Zarf.
- **This repo** provides the workload: the model as a signed OCI artifact,
  the llama.cpp inference server manifests, KEDA scaling, dashboards, the
  Zarf packages for the airgapped cluster, load tests and results.

## Results

To come: tokens/s, time to first token, p50/p95 latency, cold start and cost
per million tokens on this hardware (8-core Ryzen 7 8845HS, CPU only).

## Decisions

Architecture decision records are in [docs/adr/](docs/adr/).

## License

[MIT](LICENSE). Model weights are not in this repo; each model's source,
license and SHA-256 are recorded in the docs.

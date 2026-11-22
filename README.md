# local-airgap-LLM

An openly licensed, quantized LLM served on CPU inside the
[platform-lab](https://github.com/JGGHalfhide/platform-lab) Kubernetes
platform, delivered through the same supply chain as every image (signed,
verified at admission), scaled and observed, and deployed to an airgapped
cluster with no internet.

[![ci](https://github.com/JGGHalfhide/local-airgap-LLM/actions/workflows/ci.yml/badge.svg)](https://github.com/JGGHalfhide/local-airgap-LLM/actions/workflows/ci.yml)

**Status:** Week 3 of 6: an OpenAI-compatible endpoint on the build cluster, synced by Argo CD, the model verified at admission ([docs/stack.md](docs/stack.md)). See [ROADMAP.md](ROADMAP.md).

## Architecture

```mermaid
flowchart LR
  subgraph up["Upstream"]
    hf[("Hugging Face<br/>pinned GGUF")]
    ghcr[("ghcr.io<br/>llama.cpp server")]
  end

  subgraph host["Build host"]
    mm["make model<br/>SHA-256 check, OCI image,<br/>cosign sign + SLSA provenance"]
    mi["make image (platform-lab)<br/>SBOM, Trivy gate, cosign"]
    zp["Zarf packages<br/>(Week 5)"]
  end

  subgraph gh["GitHub"]
    repo["deploy/<br/>Deployment, Service,<br/>NetworkPolicy"]
  end

  subgraph build["Build cluster: RKE2 CIS, platform-lab"]
    zot[("Zot")]
    argo["Argo CD"]
    kyv["Pod Security + Kyverno<br/>signature check on<br/>image + model volume"]
    srv["llama-server<br/>model mounted read-only,<br/>no egress"]
    cli["Client pods<br/>(labelled)"]
  end

  subgraph ag["Airgap VM (Week 5)"]
    zreg[("Zarf registry")]
    srv2["llama-server"]
  end

  hf --> mm --> zot
  ghcr --> mi --> zot
  repo -- "GitOps sync" --> argo --> kyv --> srv
  zot -- "signed digests" --> srv
  cli -- "OpenAI API :8080" --> srv
  zot --> zp -- "signed packages" --> zreg --> srv2
```

![local-airgap-LLM overview: model chosen by measurement, model as a signed image, OpenAI-compatible serving, verified at admission, airgapped deploy, and the measured results](docs/images/overview.png)

Every tool, its version, why it was chosen, the pipeline and the data flow:
[docs/stack.md](docs/stack.md).

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

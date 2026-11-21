# local-airgap-LLM roadmap

Goal: an openly licensed, quantized LLM served on CPU inside the platform-lab
Kubernetes platform, delivered through the same supply chain as every image
(signed, verified at admission), scaled and observed, and deployed to an
airgapped cluster with no internet. Measured: tokens/s, latency, cold start,
cost per million tokens on this hardware.

Six weeks at about 7-10 hours per week. Each week ends with a rebuild check
and a tag, like platform-lab.

## Week 1: Foundations and model choice

Goal: repo, second cluster sized, and a model picked by measurement.

- [x] Git repo (GitHub), README stub, LICENSE, .gitignore (NOTES.txt, models/, build/, kubeconfig*), yamllint, CI skeleton
- [x] Resource plan for two VMs on ~28 GiB usable: build-node-01 and the airgap target (vCPU, RAM, disk for model blobs in Zot and on nodes). Record in docs/. (11/21: docs/resource-plan.md, applied.)
- [x] platform-lab change: turn `airgap_rehearsal` into a sized airgap target (vars for vCPU/RAM/disk; maybe rename), bring it up with `make airgap-up`-style flow on the VM. Note in both repos. (11/21: resized to 8 vCPU / 12 GiB / 60 GiB, VM up, platform-lab b293cda; RKE2 on it comes in Week 5.)
- [x] Benchmark candidate models on this CPU (llama.cpp `llama-bench`, first on the host, then inside the build VM to see VM overhead): 3-4 openly licensed GGUF models, 3-8B, Q4_K_M (and one Q8 for comparison). Record prompt and generation tokens/s, RAM, load time. (11/21: host and in-cluster Job on build-node-01; benchmarks/README.md.)
- [x] ADR 0001: runtime (llama.cpp server vs alternatives). ADR 0002: model choice from the benchmark (license, size, speed, quality notes). (11/21: Qwen3.5 4B Q4_K_M.)
- [ ] Tag v0.1

Acceptance: both VMs sized and documented; a model chosen with numbers behind it.

## Week 2: The model through the supply chain

Goal: the model is a signed, verifiable artifact in Zot, like the images.

- [ ] Model as an OCI artifact in Zot (ORAS, or an OCI image wrapping the GGUF for image volumes; decide which, ADR)
- [ ] Provenance recorded with it: source URL, license, upstream SHA-256, quantization
- [ ] Sign with the lab cosign key (same flags as platform-lab: no Rekor); attach a provenance/SBOM-style attestation
- [ ] Runtime image (llama.cpp server) through platform-lab's `make image` (SBOM, Trivy gate, sign)
- [ ] `make model` (pull, verify checksum, package, push, sign) reusing platform-lab scripts where possible
- [ ] Tag v0.2

Acceptance: model and runtime in Zot, both signed; tampered or unsigned model detectable.

## Week 3: Serving on the build cluster

Goal: an OpenAI-compatible endpoint running under the platform's policies.

- [ ] How the pod gets the model: Kubernetes image volume (stable since Kubernetes 1.36, needs containerd 2.1+; RKE2 v1.36.5 ships containerd 2.3.4, so it should work) vs init container pulling the artifact into an emptyDir/PVC. ADR.
- [ ] Serving layer ADR: plain Deployment + Service vs KServe (InferenceService, its llama.cpp/custom runtime, scale-to-zero) and llm-d. Compare footprint on one small node, airgap packaging (extra images/CRDs in Zarf), fit with Kyverno/restricted PSS, and overlap with KEDA. Option: deploy KServe alongside and serve the same model both ways for a measured comparison.
- [ ] llama.cpp server Deployment + Service via Argo CD (platform-lab root app gets an Application pointing at this repo)
- [ ] Restricted Pod Security, sized requests/limits, labels, no Secrets in env: passes platform-lab's Kyverno policies as-is, or scoped exceptions documented
- [ ] Kyverno: model artifact signature verified at admission (extend verify-platform-images, or a new policy in platform-lab)
- [ ] NetworkPolicy: no egress from the inference pods; ingress only from allowed clients
- [ ] Smoke test: chat completion through the API; unsigned model rejected
- [ ] Tag v0.3

Acceptance: a completion from the in-cluster endpoint; an unsigned model is rejected at admission.

## Week 4: Observability and autoscaling

Goal: see it, scale it, measure cold start.

- [ ] llama.cpp metrics scraped (ServiceMonitor); inference dashboard as code: tokens/s, time to first token, p50/p95 latency, requests in flight/deferred, memory
- [ ] KEDA via Argo CD (restricted PSS, pinned, signed images)
- [ ] ScaledObject on queue depth / requests in flight; scale-to-zero
- [ ] Measure cold start (0 -> ready -> first token) and scale-up behaviour on one node
- [ ] Logs in Loki (no prompt content logged by default; decide and document)
- [ ] Tag v0.4

Acceptance: dashboard with live data; measured cold start; scale to zero and back.

## Week 5: Airgap

Goal: the same thing on the disconnected VM.

- [ ] Zarf package(s): runtime, model artifact (and its signatures), KEDA, manifests, dashboard
- [ ] Package created and signed on the build host; deployed with egress blocked (platform-lab airgap flow)
- [ ] Signatures verified via the Zarf registry; unsigned model rejected; 0 pull errors
- [ ] Completion served on the airgapped cluster; dashboard with data
- [ ] docs/airgap-runbook.md
- [ ] Tag v0.5

Acceptance: completions on the airgapped VM with egress blocked, signed model verified, no pull errors.

## Week 6: Load test, results, publish

Goal: numbers and a repo a stranger can follow.

- [ ] Load test (k6 or a small script): concurrency 1/2/4/8; tokens/s, TTFT, p50/p95, errors
- [ ] Cost per million tokens on this hardware (measured power draw x time); optional short rented-GPU comparison (user's call: off personal hardware)
- [ ] README: architecture (Mermaid), results table, quickstart (depends on platform-lab v1.0+)
- [ ] ADRs complete; gitleaks; employer check
- [ ] Full rebuild of both clusters from platform-lab + this repo; record times
- [ ] Tag v1.0; post (user's)

Acceptance: a fresh clone of both repos reproduces the endpoint on both clusters.

## Open questions

- VM sizing: how much RAM can the airgap target get while the build VM runs? Do both need to run at once (only for packaging/transfer)?
- Image volumes vs init container for the model (Week 3). Image volumes are stable in 1.36, so the open part is: does Kyverno 1.19 verify image-volume references (or only container images)?
- One model or two (a small fast one plus a larger one) for comparison?
- iGPU (Radeon 780M via Vulkan) as a stretch goal, or never?
- Does the inference endpoint need auth (API key from Vault) even inside the cluster? (Phase 3 agents will be in-cluster clients, so decide with them in mind.)
- Model choice should weigh tool calling / structured output and speed at long context, not just tokens/s on an empty prompt: Phase 3 agents depend on both.

## After this: Phase 3, agents on Kubernetes

A separate repo and project, planned after v1.0: airgapped agents running in
Kubernetes, using this repo's in-cluster endpoint. Agent workloads that call the in-cluster
endpoint: per-agent identity (ServiceAccount, RBAC), tools behind
NetworkPolicy and Kyverno, prompts and tool configs versioned through GitOps,
every model and tool call traced. Candidate first agent: an incident triage
agent with retrieval over platform-lab's runbooks and recovery docs, with a
small evaluation set. Bigger VMs; no iMac.

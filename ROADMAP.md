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
- [x] Tag v0.1

Acceptance: both VMs sized and documented; a model chosen with numbers behind it.

## Week 2: The model through the supply chain

Goal: the model is a signed, verifiable artifact in Zot, like the images.

- [x] Model as an OCI artifact in Zot (ORAS, or an OCI image wrapping the GGUF for image volumes; decide which, ADR) (11/21: ADR 0003, OCI image + image volume; platform-lab policies now cover image volumes. docs/model-image.md.)
- [x] Provenance recorded with it: source URL, license, upstream SHA-256, quantization (manifest annotations + SLSA v1 attestation.)
- [x] Sign with the lab cosign key (same flags as platform-lab: no Rekor); attach a provenance/SBOM-style attestation (tampered copy rejected at admission, docs/scans/model-admission-test.txt.)
- [x] Runtime image (llama.cpp server) through platform-lab's `make image` (SBOM, Trivy gate, sign) (11/21, during Week 1.)
- [x] `make model` (pull, verify checksum, package, push, sign) reusing platform-lab scripts where possible (uses platform-lab's scripts/lib.sh.)
- [x] Tag v0.2

Acceptance: model and runtime in Zot, both signed; tampered or unsigned model detectable.

## Week 3: Serving on the build cluster

Goal: an OpenAI-compatible endpoint running under the platform's policies.

- [x] How the pod gets the model: Kubernetes image volume (stable since Kubernetes 1.36, needs containerd 2.1+; RKE2 v1.36.5 ships containerd 2.3.4, so it should work) vs init container pulling the artifact into an emptyDir/PVC. ADR. (11/21: decided in ADR 0003, image volume. 11/22: live in the Deployment, model loaded in 4 s from the mount.)
- [x] Serving layer ADR: plain Deployment + Service vs KServe (InferenceService, its llama.cpp/custom runtime, scale-to-zero) and llm-d. Compare footprint on one small node, airgap packaging (extra images/CRDs in Zarf), fit with Kyverno/restricted PSS, and overlap with KEDA. Option: deploy KServe alongside and serve the same model both ways for a measured comparison. (11/22: ADR 0004, plain Deployment + Service; KServe Standard mode needs cert-manager + a gateway and has no scale from zero, llm-d's CPU path needs 64 cores; measured comparison skipped, reasons in the ADR.)
- [x] llama.cpp server Deployment + Service via Argo CD (platform-lab root app gets an Application pointing at this repo) (11/22: deploy/ applied by hand and tested; platform-lab 5ecef14 adds the Application; Argo CD adopted the resources, Synced/Healthy, smoke test passes.)
- [x] Restricted Pod Security, sized requests/limits, labels, no Secrets in env: passes platform-lab's Kyverno policies as-is, or scoped exceptions documented (11/22: passes as-is, no exceptions.)
- [x] Kyverno: model artifact signature verified at admission (extend verify-platform-images, or a new policy in platform-lab) (11/21, platform-lab 250acd1: verify-platform-images covers image volumes.)
- [x] NetworkPolicy: no egress from the inference pods; ingress only from allowed clients (11/22: default deny; ingress from pods and namespaces labelled inference.local-airgap-llm/client=true, and Prometheus.)
- [x] Smoke test: chat completion through the API; unsigned model rejected (11/22: `make smoke`, docs/scans/week3-smoke-test.txt.)
- [x] Tag v0.3

Acceptance: a completion from the in-cluster endpoint; an unsigned model is rejected at admission.

## Week 4: Observability and autoscaling

Goal: see it, scale it, measure cold start.

- [x] llama.cpp metrics scraped (ServiceMonitor); inference dashboard as code: tokens/s, time to first token, p50/p95 latency, requests in flight/deferred, memory (11/22: ServiceMonitor, `make dashboard` generates the JSON. llama-server has no latency histograms, so TTFT and p50/p95 are client side: cold-start script now, load test in Week 6. docs/observability.md.)
- [x] KEDA via Argo CD (restricted PSS, pinned, signed images) (11/22: KEDA 2.21.1 + HTTP add-on 0.16.0 in platform-lab gitops/keda, 578f372: release manifests + patches, six images signed in Zot, projected tokens, 48/48 policy checks; the add-on's images under a scoped Trivy exception for CVE-2026-77524. ADR 0005.)
- [x] ScaledObject on queue depth / requests in flight; scale-to-zero (11/22: InterceptorRoute on concurrency, 4 per replica; ScaledObject 0-1, 5 min cooldown; clients call llm-api.llm.svc, the server admits only the interceptor.)
- [x] Measure cold start (0 -> ready -> first token) and scale-up behaviour on one node (11/22: 6.2-6.4 s from a request at zero to the first byte, 6.7 s to a short reply, 3 runs; warm 0.2 s; back to zero 5 min after the last request. docs/scaling.md.)
- [x] Logs in Loki (no prompt content logged by default; decide and document) (11/22: default verbosity logs no prompt or response text, checked in Loki; debug flags never in deploy/. docs/observability.md. Found a platform-lab inotify limit issue, open there.)
- [x] Tag v0.4

Acceptance: dashboard with live data; measured cold start; scale to zero and back.

## Week 5: Airgap

Goal: the same thing on the disconnected VM.

- [x] Zarf package(s): runtime, model artifact (and its signatures), KEDA, manifests, dashboard (11/22: zarf/llm + zarf/llm-smoke here, zarf/keda in platform-lab; .sig/.att listed explicitly.)
- [x] Package created and signed on the build host; deployed with egress blocked (platform-lab airgap flow) (11/22: `make airgap-packages`, `make airgap-deploy` via platform-lab's script and zarf role; 7m48s.)
- [x] Signatures verified via the Zarf registry; unsigned model rejected; 0 pull errors (11/22: needed a platform-lab fix, the airgap policy variant ignored image volumes.)
- [x] Completion served on the airgapped cluster; dashboard with data (11/22: make smoke 7/7; cold start 5.9-6.8 s.)
- [x] docs/airgap-runbook.md
- [x] Tag v0.5

Acceptance: completions on the airgapped VM with egress blocked, signed model verified, no pull errors.

## Week 6: Load test, results, publish

Goal: numbers and a repo a stranger can follow.

- [x] Load test (k6 or a small script): concurrency 1/2/4/8; tokens/s, TTFT, p50/p95, errors (11/23: scripts/load-test.py, stdlib, through the interceptor. 18.2 tok/s at 4 concurrent, 14.3 one stream, TTFT 1.9 s; 0/64 errors. Found and fixed an OOMKill under load: RAM prompt cache, `--cache-ram 0`, c362a5a. docs/load-test.md.)
- [x] Cost per million tokens on this hardware (measured power draw x time); optional short rented-GPU comparison (user's call: off personal hardware) (11/23: RAPL CPU package energy, 0.75 kWh per million output tokens at 4 concurrent, 1.27 one stream; kWh only, no tariff; no wall meter, so a lower bound. GPU comparison skipped, user's call.)
- [x] README: architecture (Mermaid), results table, quickstart (depends on platform-lab v1.0+) (11/23; quickstart to be checked by the full rebuild.)
- [x] ADRs complete; gitleaks; employer check (11/23: ADRs 0001-0005 cover runtime, model, model packaging, serving layer, scaling; gitleaks in CI, 18 commits, no leaks; employer check: only lab names and public upstreams, signed off by the user.)
- [x] Full rebuild of both clusters from platform-lab + this repo; record times (11/23: make down, fresh clones of both repos, every step from the READMEs and runbook: build cluster 8m16s make up, 13/13 apps Healthy, smoke passed; model image rebuilt to the same digest; airgap VM: airgap-up 4m09s, airgap-deploy 7m53s, smoke 7/7, cold start 5.8-6.7 s, 0 pull errors. About 47 min of steps for both. Found and fixed: tag-only image publishing (postgres tag moved; quickstarts now tag@digest and complete), smoke exit code on the airgapped cluster (platform-lab lib.sh), runbook VM creation, smoke rerun race. docs/scans/week6-rebuild.txt.)
- [x] Tag v1.0; post (user's) (v1.0 tagged 11/23; the post is the user's.)

Acceptance: a fresh clone of both repos reproduces the endpoint on both clusters.

## v1.1: thinking default (11/23)

Goal: the server's default matches what was measured and cannot hang a slot.

- [x] Measure thinking on vs off, server-wide switches, budgets (11/23: `scripts/thinking-check.py`, `toolcall-check.py --thinking`; benchmarks/README.md. Thinking fixed 3/3 multi-step errors but looped to 8k tokens with no answer on 3/3 open-ended triage questions; budget 1024: 10/10 answered, 9/10 right.)
- [x] ADR 0006: `--reasoning off --reasoning-budget 1024` in deploy/; smoke test checks no thinking by default; zarf/llm 0.6.0
- [x] Deployed and smoke-tested on the build cluster and the airgapped cluster (11/23: build cluster via Argo CD, smoke 8/8, "Paris" in 2 tokens with no reasoning. Airgap: zarf/llm 0.6.0 on a recreated airgap VM, smoke 8/8, 0 pull errors. On the way: smoke-test egress probes no longer pass without a pod; a KEDA operator restart scales to zero at once (docs/scaling.md); platform-lab's zarf role no longer reads a failed record read as "not deployed"; an interrupted `zarf init` cannot be re-run under the policies, open in platform-lab.)
- [x] Tag v1.1

## Open questions

- ~~Qwen3.5 thinks before answering by default (134 completion tokens for a one-word answer in the smoke test). Turn thinking off per request (`chat_template_kwargs`) or server-wide for agent use? Measure the latency cost in Week 4/6.~~ Answered 11/23 (v1.1, ADR 0006): off server-wide, opt-in per request with a 1024-token budget. When agents should opt in is Phase 3's.

- ~~VM sizing: how much RAM can the airgap target get while the build VM runs? Do both need to run at once (only for packaging/transfer)?~~ Answered 11/22: both VMs 8 vCPU / 16 GiB, run one at a time; Zarf packages are built on the host while the build VM runs, then the VMs are switched (docs/resource-plan.md).
- ~~Image volumes vs init container for the model.~~ Answered 11/21 (ADR 0003): Kyverno 1.19 verifies image volumes only through a custom extractor; platform-lab's image policies now include them.
- One model or two (a small fast one plus a larger one) for comparison? Moved to Phase 3: one model (Qwen3.5 4B) for Phase 2; whether agents need a second model is for Phase 3's evaluation set.
- iGPU (Radeon 780M via Vulkan) as a stretch goal, or never? Deferred: not done in Phase 2 (CPU-only, decided 11/20); revisit only if Phase 3 needs more speed.
- ~~Does the inference endpoint need auth (API key from Vault) even inside the cluster? (Phase 3 agents will be in-cluster clients, so decide with them in mind.) Moved to Phase 3, an early decision there; today access is limited by NetworkPolicy to labelled client pods through the interceptor. If an API key is added, it is a change to this repo's endpoint (like v1.1).~~ Answered in local-agents 11/23: NetworkPolicy only, no API key (local-agents ADR 0001); no change here.
- ~~Model choice should weigh tool calling / structured output and speed at long context, not just tokens/s on an empty prompt: Phase 3 agents depend on both.~~ Weighed in Week 1 (ADR 0002: tool-call checks 5/5, speed at 4k context), at long context on 11/22 (a 29,713-token prompt answered correctly, docs/resource-plan.md) and in v1.1 (tool calls with thinking on and off). Reasoning quality on real agent tasks is for Phase 3's evaluation set.

## After this: Phase 3, agents on Kubernetes

Continues in its own repo, local-agents (~/projects/local-agents, set up
11/23; git repo created in its Week 1): airgapped agents in Kubernetes that
call this repo's endpoint (`http://llm-api.llm.svc:8080`). Its ROADMAP.md
has the plan, starting with an evaluation set of real Phase 1-2 incidents.
Changes it needs here (endpoint access, flags, a second model) are made in
this repo, with its conventions, and noted in both.

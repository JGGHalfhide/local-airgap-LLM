# 0004. Serve with a plain Deployment and Service

Status: accepted (Week 3, 11/22)

## Context

llama-server (ADR 0001) serves one model (ADR 0002), mounted as an image
volume (ADR 0003), on one single-node RKE2 cluster per site: build-node-01
(8 vCPU / 12 GiB) and the airgap target (8 vCPU / 12 GiB). Everything that
runs has to be signed, pinned by digest, pass platform-lab's Kyverno
policies and restricted Pod Security, and travel to the airgapped cluster
in a Zarf package. Week 4 adds KEDA for queue-based scaling and scale to
zero.

Options considered (checked 11/22):

- **Plain Deployment + Service**, synced by Argo CD from this repo. One
  image (already signed), no CRDs or controllers, nothing to add to the
  Zarf package beyond the manifests. Scaling comes from KEDA (Week 4),
  which can scale a Deployment to zero and back.
- **KServe** (v0.21.0, Standard mode, which runs plain Deployments without
  Knative or Istio). Adds CRDs, a controller with admission webhooks,
  cert-manager 1.15+ for the webhook certificates, and Gateway API or an
  Ingress for routing: about half a dozen more images to publish, sign and
  carry through Zarf, and a controller that has to pass the restricted
  policies. Standard mode does not scale from zero for HTTP requests
  (KServe docs, Kubernetes deployment guide); it uses HPA, or KEDA for
  custom metrics, so KEDA is still needed. There is no llama.cpp runtime in
  a release (kserve/kserve#6542, opened 11/2, still open), so llama-server
  would run as a custom ServingRuntime with the same container spec as a
  plain Deployment. What KServe adds (a common InferenceService API across
  model servers, canary rollouts, model storage initializers) matters with
  many models and teams, not with one model on one node.
- **llm-d** (1.0, 10/20). Built around vLLM, an inference-aware gateway
  and disaggregated prefill/decode across GPU nodes. Its CPU path needs a
  4th-gen Xeon or equivalent and at least 64 cores and 64 GB per replica
  (llm-d docs, accelerators page): far above this hardware, and the
  runtime is not llama.cpp.

## Decision

Serve with a plain Deployment and a ClusterIP Service in this repo's
`deploy/` directory, synced by an Argo CD Application in platform-lab's
root app. No serving framework; Week 4's KEDA ScaledObject targets the
Deployment directly.

The measured side-by-side with KServe offered in the roadmap is not done:
the extra footprint (cert-manager, gateway, controller) does not fit next
to the model on a 12 GiB node without displacing the monitoring stack, and
the result would not change the decision for one model on one node.
Revisit if Phase 3 serves several models or needs canary rollouts.

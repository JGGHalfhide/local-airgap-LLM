# 0005. Scale to zero with KEDA and its HTTP add-on

Status: accepted (Week 4, 11/22)

## Context

The inference server holds 5-6 GiB while idle (ADR 0002, 32k context) on a
16 GiB node. The roadmap asks for scaling on requests in flight or queue
depth, scale to zero, and a measured cold start. Only one replica fits on
the node, so in practice scaling means 0 and 1.

KEDA alone cannot wake an HTTP server from zero: with no pod there are no
llama-server metrics to read and nothing that accepts the request. Options
(checked 11/22):

- **KEDA core with a Prometheus trigger** on `llamacpp:requests_processing`.
  Works from 1 replica up; at 0 requests fail until something scales the
  Deployment by hand. No scale to zero.
- **KEDA core + KEDA HTTP add-on** (v2.21.1 + v0.16.0). The add-on's
  interceptor is a proxy in front of the Service: it counts requests in
  flight per route, holds them while the backend scales from zero (per
  route readiness timeout and pending limit), and its scaler feeds the
  count to KEDA. Three more components (operator, interceptor, scaler),
  about 20 MiB each at rest. The project calls itself beta and stable,
  with minor API changes possible before 1.0 (HTTPScaledObject was
  replaced by InterceptorRoute in 0.14).
- **Knative Serving** (KServe's serverless mode): scale to zero built in,
  but a larger control plane and a networking layer; ruled out with
  KServe in ADR 0004.

Supply chain: the six images go through platform-lab's `make image`. KEDA
core 2.21.1 passed the Trivy gate. The add-on 0.16.0 images failed on
CVE-2026-77524 (CRITICAL: KEDA operator token sent to a tenant-chosen Vault
via a TriggerAuthentication), from KEDA v2.18.3 compiled in as a library.
The add-on binaries link only KEDA's API types, external-scaler gRPC and
metrics-webhook packages, with no reference to the vulnerable resolver
code (checked in the binaries); KEDA core, which has that code, is
patched. Upstream main already uses KEDA 2.21.0, not yet released.

## Decision

Use KEDA core with the HTTP add-on.

- platform-lab installs both (cluster-wide CRDs, webhooks, the external
  metrics APIService) as `gitops/keda`: pinned release manifests plus
  patches (images from the platform registry by digest, projected API
  tokens, labels, one interceptor). The CVE gets a Trivy exception scoped
  to the three add-on binaries, to be removed when a release ships
  KEDA >= 2.21.
- This repo owns the scaling objects (`deploy/llm/scaling.yaml`): an
  InterceptorRoute to the llama-server Service (concurrency target 4, the
  server's slots; readiness timeout 180 s; up to 32 held requests) and a
  ScaledObject with 0-1 replicas and a 5-minute cooldown.
- Clients call `llm-api.llm.svc:8080`, an ExternalName Service for the
  interceptor. llama-server admits only the interceptor and Prometheus, so
  no request bypasses the count that keeps it running. The client label
  check moves to the interceptor (a NetworkPolicy in platform-lab).
- Argo CD no longer sets `replicas` on the Deployment.

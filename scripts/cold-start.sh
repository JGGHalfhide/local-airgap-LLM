#!/usr/bin/env bash
# Cold start of the inference endpoint: from a request while scaled to zero
# to the first streamed token (Week 4).
#
# Waits until KEDA has scaled llama-server to zero (5 min cooldown after the
# last request, so up to ~6 min), then sends one streamed chat completion
# through llm-api from a labelled client pod and reports:
#   - time to first byte (response headers: sent once the backend accepts
#     the request, so for a cold start it covers the wake-up) and to the end
#     of a short reply (an upper bound on the first token), client side,
#   - the pod's timeline from its status: created -> scheduled -> container
#     started -> ready (model loaded, /health 200).
# Then sends the same request warm, for comparison.
#
# Usage: KUBECONFIG=... scripts/cold-start.sh [runs]   (default 1)
# Creates the namespace llm-coldstart and deletes it at the end.
set -euo pipefail

runs=${1:-1}
ns=llm-coldstart
api=http://llm-api.llm.svc:8080
# The runtime image (signed, in the platform registry) has curl.
image=$(grep -o '127.0.0.1:30500/ggml-org/llama.cpp@sha256:[0-9a-f]*' \
  "$(cd "$(dirname "$0")/.." && pwd)/deploy/llm/llama-server.yaml")

cleanup() { kubectl delete ns "$ns" --ignore-not-found --wait=false >/dev/null; }
trap cleanup EXIT
# A namespace left terminating by a previous run would swallow the new pod.
kubectl delete ns "$ns" --ignore-not-found --wait=true --timeout=3m >/dev/null

{
  cat <<EOF
apiVersion: v1
kind: Namespace
metadata:
  name: $ns
  labels:
    inference.local-airgap-llm/client: "true"
    pod-security.kubernetes.io/enforce: restricted
EOF
  "$(dirname "$0")/zarf-pull-secret.sh" "$ns"   # airgapped cluster only
  cat <<EOF
---
apiVersion: v1
kind: Pod
metadata:
  name: client
  namespace: $ns
  labels:
    app.kubernetes.io/name: llm-coldstart-client
    inference.local-airgap-llm/client: "true"
spec:
  automountServiceAccountToken: false
  securityContext:
    runAsNonRoot: true
    runAsUser: 65532
    runAsGroup: 65532
    seccompProfile:
      type: RuntimeDefault
  containers:
    - name: main
      image: $image
      command: ["sleep", "3600"]
      resources:
        requests: {cpu: 10m, memory: 16Mi}
        limits: {memory: 64Mi}
      securityContext:
        allowPrivilegeEscalation: false
        readOnlyRootFilesystem: true
        capabilities:
          drop: ["ALL"]
EOF
} | kubectl apply -f - >/dev/null
kubectl -n "$ns" wait --for=condition=Ready pod/client --timeout=2m >/dev/null

body='{"model":"qwen3.5-4b","stream":true,"max_tokens":16,"temperature":0,"chat_template_kwargs":{"enable_thinking":false},"messages":[{"role":"user","content":"Say hello in one word."}]}'

# Prints "<time to first byte> <total> <HTTP code>"; times in seconds.
request() {
  kubectl -n "$ns" exec client -- curl -s -o /dev/null -m 300 \
    -w '%{time_starttransfer} %{time_total} %{http_code}\n' \
    -H 'Content-Type: application/json' -d "$body" "$api/v1/chat/completions"
}

ts() { date -d "$1" +%s; }

for run in $(seq 1 "$runs"); do
  echo "## run $run"
  echo -n "waiting for zero replicas..."
  for _ in $(seq 1 120); do
    kubectl -n llm get pods -l app.kubernetes.io/name=llama-server --no-headers 2>/dev/null | grep -q . || break
    sleep 5
  done
  if kubectl -n llm get pods -l app.kubernetes.io/name=llama-server --no-headers 2>/dev/null | grep -q .; then
    echo " still running after 10 min"; exit 1
  fi
  echo " ok"

  t0=$(date +%s)
  read -r ttfb total code <<<"$(request)"
  pod=$(kubectl -n llm get pods -l app.kubernetes.io/name=llama-server -o jsonpath='{.items[0].metadata.name}')
  created=$(kubectl -n llm get pod "$pod" -o jsonpath='{.metadata.creationTimestamp}')
  scheduled=$(kubectl -n llm get pod "$pod" -o jsonpath='{.status.conditions[?(@.type=="PodScheduled")].lastTransitionTime}')
  started=$(kubectl -n llm get pod "$pod" -o jsonpath='{.status.containerStatuses[0].state.running.startedAt}')
  ready=$(kubectl -n llm get pod "$pod" -o jsonpath='{.status.conditions[?(@.type=="Ready")].lastTransitionTime}')
  echo "cold: HTTP $code, first byte ${ttfb}s, reply done ${total}s (client side)"
  echo "pod timeline from the request (s, 1 s resolution): created +$(( $(ts "$created") - t0 )), scheduled +$(( $(ts "$scheduled") - t0 )), container started +$(( $(ts "$started") - t0 )), ready +$(( $(ts "$ready") - t0 ))"
  read -r ttfb total code <<<"$(request)"
  echo "warm: HTTP $code, first byte ${ttfb}s, reply done ${total}s"
done

#!/usr/bin/env bash
# Smoke test of the in-cluster inference endpoint (deploy/, Weeks 3-4).
#
#  1. A chat completion from a labelled client pod through llm-api (the KEDA
#     HTTP add-on's interceptor), which also wakes the server if KEDA has
#     scaled it to zero; the time includes that cold start.
#  2. A pod without the client label cannot reach llm-api, and a labelled
#     client cannot bypass the interceptor to reach llama-server directly.
#  3. The inference pod has no egress (DNS, internet, other pods).
#  4. The same Deployment with an unsigned copy of the model image is
#     rejected at admission (server-side dry run: nothing is created). The
#     copy is the signed image with one annotation added (crane mutate), so
#     it has a new digest and no signature; it is pushed to the registry as
#     models/<name>:unsigned-smoke-test (layers shared, same digest each run)
#     so the rejection is for the signature, not a missing image.
#
# Usage: KUBECONFIG=... scripts/smoke-test.sh
# Needs platform-lab next to this repo (registry login via its lib.sh).
# Creates the namespace llm-smoke and deletes it at the end. Exits non-zero
# if any check fails.
set -euo pipefail

ns=llm-smoke
api=http://llm-api.llm.svc:8080
direct=http://llama-server.llm.svc:8080
alpine=127.0.0.1:30500/library/alpine:3.22@sha256:49340b346798875b5592e5d420241f2f9cfba5f47e62ebb1446a1b34734e898f
root=$(cd "$(dirname "$0")/.." && pwd)
PLATFORM_LAB="${PLATFORM_LAB:-$(cd "$root/../platform-lab" && pwd)}"
source "$PLATFORM_LAB/scripts/lib.sh"   # sets REPO_ROOT to platform-lab
fail=0
pass() { echo "PASS: $*"; }
nok() { echo "FAIL: $*"; fail=1; }

cleanup() {
  kubectl delete ns "$ns" --ignore-not-found --wait=false >/dev/null
  registry_env_cleanup
}
trap cleanup EXIT

echo "## setup"
# pod <name> <client: true|false>: a restricted pod that only sleeps.
pod() {
  cat <<EOF
---
apiVersion: v1
kind: Pod
metadata:
  name: $1
  namespace: $ns
  labels:
    app.kubernetes.io/name: llm-smoke-$1
    inference.local-airgap-llm/client: "$2"
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
      image: $alpine
      command: ["sleep", "600"]
      resources:
        requests: {cpu: 10m, memory: 16Mi}
        limits: {memory: 32Mi}
      securityContext:
        allowPrivilegeEscalation: false
        readOnlyRootFilesystem: true
        capabilities:
          drop: ["ALL"]
EOF
}
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
  pod client true
  pod not-a-client false
} | kubectl apply -f -
kubectl -n "$ns" wait --for=condition=Ready pod/client pod/not-a-client --timeout=2m

echo
echo "## 1. chat completion from a client pod (via llm-api)"
echo "replicas before: $(kubectl -n llm get deploy llama-server -o jsonpath='{.status.readyReplicas}')"
body='{"model":"qwen3.5-4b","messages":[{"role":"user","content":"Reply with one word: the capital of France."}],"max_tokens":256,"temperature":0}'
start=$(date +%s%N)
resp=$(kubectl -n "$ns" exec client -- wget -q -O - \
  --header 'Content-Type: application/json' --post-data "$body" -T 300 "$api/v1/chat/completions") || resp=""
secs=$(python3 -c "print(round(($(date +%s%N) - $start) / 1e9, 1))")
if echo "$resp" | python3 -c 'import json,sys; r=json.load(sys.stdin); c=r["choices"][0]["message"]["content"]; t=r.get("timings",{}); print("model:", r["model"]); print("answer:", c.strip()); print("usage:", r["usage"]); print("prompt tok/s: %.1f, generation tok/s: %.1f" % (t.get("prompt_per_second",0), t.get("predicted_per_second",0))); sys.exit(0 if "paris" in c.lower() else 1)'; then
  pass "completion in ${secs}s (wall clock, incl. kubectl exec)"
else
  nok "no valid completion: ${resp:0:300}"
fi

echo
echo "## 2. who can reach the endpoint"
if kubectl -n "$ns" exec not-a-client -- wget -q -O /dev/null -T 5 "$api/health" 2>/dev/null; then
  nok "unlabelled pod reached llm-api"
else
  pass "unlabelled pod blocked at llm-api (timeout)"
fi
if kubectl -n "$ns" exec client -- wget -q -O /dev/null -T 5 "$direct/health" 2>/dev/null; then
  nok "client reached llama-server directly, bypassing the interceptor"
else
  pass "client cannot bypass the interceptor (timeout)"
fi

echo
echo "## 3. egress from the inference pod"
# Needs a running pod (check 1 woke it; KEDA keeps it 5 min): without one,
# kubectl exec fails and would look like a blocked connection.
if [ "$(kubectl -n llm get deploy llama-server -o jsonpath='{.status.readyReplicas}')" != 1 ]; then
  nok "no running llama-server pod to test egress from"
fi
for target in https://huggingface.co http://zot.zot.svc:5000/v2/ http://10.43.0.10:53; do
  if kubectl -n llm exec deploy/llama-server -- curl -s -o /dev/null -m 5 "$target" 2>/dev/null; then
    nok "inference pod reached $target"
  else
    pass "inference pod cannot reach $target"
  fi
done

echo
echo "## 4. unsigned model image at admission"
# The model reference in the Deployment: 127.0.0.1:30500/models/<name>@<digest>.
signed=$(grep -o 'models/[^@]*@sha256:[0-9a-f]*' "$root/deploy/llm/llama-server.yaml")
repo=${signed%@*}
registry_env
unsigned=$(crane mutate "$REGISTRY/$signed" \
  --annotation io.github.jgghalfhide.local-airgap-llm.smoke-test=unsigned \
  -t "$REGISTRY/$repo:unsigned-smoke-test" 2>/dev/null)
unsigned=${unsigned##*@}
echo "signed:   $signed"
echo "unsigned: $repo@$unsigned"
if out=$(kubectl kustomize "$root/deploy" | sed "s|$signed|$repo@$unsigned|" \
    | kubectl apply --dry-run=server -f - 2>&1); then
  nok "unsigned model admitted"
elif echo "$out" | grep -q "not signed with the platform-lab cosign key"; then
  echo "$out" | grep -o 'denied the request: .*'
  pass "unsigned model rejected: no signature"
else
  nok "rejected, but not for the signature: $out"
fi

echo
[ "$fail" = 0 ] && echo "ALL PASSED" || { echo "SOME CHECKS FAILED"; exit 1; }

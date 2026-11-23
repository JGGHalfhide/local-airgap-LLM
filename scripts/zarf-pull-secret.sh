#!/usr/bin/env bash
# Print Zarf's registry pull Secret (private-registry) for namespace $1, as a
# YAML document to apply before the namespace's pods. Airgapped cluster only;
# prints nothing elsewhere.
#
# The Zarf agent points every pod at its registry and adds
# `imagePullSecrets: [private-registry]`, but Zarf creates that Secret only
# in the namespaces that exist when its packages deploy. The test namespaces
# of smoke-test.sh and cold-start.sh are created later, so they get a copy of
# the one in llm (created by the llm package).
set -euo pipefail
ns=$1
kubectl get ns zarf >/dev/null 2>&1 || exit 0
echo "---"
kubectl -n llm get secret private-registry -o json | python3 -c '
import json, sys
s = json.load(sys.stdin)
m = s["metadata"]
s["metadata"] = {"name": m["name"], "namespace": sys.argv[1], "labels": m.get("labels", {})}
print(json.dumps(s))' "$ns"

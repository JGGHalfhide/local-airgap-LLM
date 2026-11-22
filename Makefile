# Model chosen in ADR 0002; any row of benchmarks/candidates.tsv works.
MODEL ?= qwen3.5-4b-q4km

.PHONY: model smoke lint

# Fetch, verify, package, push, sign and attest the model (ADR 0003).
# Needs platform-lab next to this repo and its registry running.
model:
	./scripts/publish-model.sh $(MODEL)

# Smoke test of the deployed endpoint (scripts/smoke-test.sh): completion,
# NetworkPolicy, no egress, unsigned model rejected. Needs KUBECONFIG.
smoke:
	./scripts/smoke-test.sh

lint:
	yamllint -s .

# Model chosen in ADR 0002; any row of benchmarks/candidates.tsv works.
MODEL ?= qwen3.5-4b-q4km

.PHONY: model smoke dashboard lint airgap-packages airgap-deploy

PLATFORM_LAB ?= $(abspath ../platform-lab)
AIRGAP_OUT := $(CURDIR)/build/airgap/packages
LLM_VERSION := $(shell sed -n 's/^  version: //p' zarf/llm/zarf.yaml)
SMOKE_VERSION := $(shell sed -n 's/^  version: //p' zarf/llm-smoke/zarf.yaml)

# Fetch, verify, package, push, sign and attest the model (ADR 0003).
# Needs platform-lab next to this repo and its registry running.
model:
	./scripts/publish-model.sh $(MODEL)

# Smoke test of the deployed endpoint (scripts/smoke-test.sh): completion,
# NetworkPolicy, no egress, unsigned model rejected. Needs KUBECONFIG.
smoke:
	./scripts/smoke-test.sh

# Regenerate the Grafana dashboard JSON (deploy/monitoring/dashboards/).
dashboard:
	python3 scripts/gen-dashboard.py

lint:
	yamllint -s .

# --- Airgap (Week 5; docs/airgap-runbook.md) ---------------------------------

# Build and sign the Zarf packages (zarf/llm, zarf/llm-smoke) with
# platform-lab's script and lab cosign key. Needs the build cluster running:
# images and signatures come from its registry.
airgap-packages:
	ZARF_SRC=$(CURDIR)/zarf OUT=$(AIRGAP_OUT) $(PLATFORM_LAB)/scripts/airgap-packages.sh llm llm-smoke

# Deploy them on the airgapped node with platform-lab's zarf role, after
# platform-lab's own packages (same run; unchanged packages are skipped).
# Run platform-lab's make airgap-up and airgap-disconnect first.
airgap-deploy:
	@test -f $(AIRGAP_OUT)/SHA256SUMS || { echo "run make airgap-packages first"; exit 1; }
	cd $(PLATFORM_LAB)/ansible && ansible-playbook airgap-deploy.yml </dev/null \
	  -e '{"zarf_extra_packages": [{"name": "llm", "version": "$(LLM_VERSION)", "dir": "$(AIRGAP_OUT)"}, {"name": "llm-smoke", "version": "$(SMOKE_VERSION)", "dir": "$(AIRGAP_OUT)"}]}'

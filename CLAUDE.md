# local-airgap-LLM

Local LLM inference on the platform-lab Kubernetes platform, deployed to an
airgapped cluster. CPU-only. Built connected on the build cluster, packaged
with Zarf, deployed with no internet to a second VM. Phase 2 of platform-lab
(v1.0 tagged 11/18), in its own repo. About 7-10 hours per week.

## Relationship to platform-lab (~/projects/platform-lab)
platform-lab provides the clusters; this repo provides the workload.
- platform-lab owns: VMs (Terraform), OS hardening, RKE2, Argo CD, Zot, Kyverno
  and its policy set, cosign key and lab CA, monitoring, Velero, Zarf init.
  Changes there (a bigger airgap target VM, an Argo CD Application pointing at
  this repo, Kyverno exceptions/policies the platform enforces) are made in
  platform-lab, with its conventions, and noted in both repos.
- This repo owns: model selection and benchmarks, the model as a signed OCI
  artifact, the inference server manifests (Argo CD reads them from here),
  KEDA scaling, inference dashboards, the Zarf packages for runtime + model,
  load tests, and the results.
- Read platform-lab's CLAUDE.md, README.md and docs/ (stack.md, supply-chain.md,
  policy-map.md, airgap-runbook.md) before changing anything that touches the
  platform. Reuse its scripts (publish-image.sh, Zarf/cosign flow) rather than
  copying them.

## Hardware
- Build host: GMKtec K8 Plus (Ryzen 7 8845HS, 8 cores / 16 threads, 32 GB RAM,
  ~28 GiB usable), Arch Linux, libvirt/QEMU/KVM at qemu:///system.
  Radeon 780M iGPU: not used (CPU-only decided 11/20; stretch goal at most).
- Build cluster VM: build-node-01, 192.168.122.10, 4 vCPU / 12 GiB today
  (platform-lab terraform vars vcpus, memory_mib).
- Airgap target: a SECOND VM on the same host (decided 11/20), replacing the
  iMac. Based on platform-lab's airgap_rehearsal module (192.168.122.20, today
  4 vCPU / 8 GiB via rehearsal_vcpus / rehearsal_memory_mib): needs resizing.
  Both VMs at once must fit in ~28 GiB with headroom for the host.
- The iMac (airgap-node-01) is retired for this project.

## Decisions so far (11/20-11/21)
- CPU-only inference.
- Model (11/21, ADR 0002): Qwen3.5 4B Q4_K_M from ggml-org/Qwen3.5-4B-GGUF,
  Apache-2.0. Fallback: Granite 4.2 3B Q4_K_M.
- Runtime (11/21, ADR 0001): llama.cpp llama-server v1.0.0, CPU build,
  --jinja --load-mode none (v1.0 renamed --no-mmap). Benchmarks in benchmarks/.
- Airgap target: second VM, not the iMac.
- Separate repo from platform-lab.

## Conventions (same as platform-lab)
- Everything reproducible from the repos: no manual steps on VMs or clusters.
- Ansible idempotent (second run changed=0); Terraform typed vars with descriptions.
- Pin every version (images by digest, tools by version + checksum, models by SHA-256).
- Run linters before committing (yamllint, plus whatever this repo adds).
- Conventional commits (feat:, fix:, docs:, chore:).
- No AI attribution in commits or PRs (no Co-Authored-By or "Generated with" lines).
- Tag the repo at the end of each week (v0.1, v0.2, ...).
- ADRs in docs/adr/NNNN-title.md: decision and context only.
- Secrets, kubeconfigs, model weights and state files are never committed.
  Secrets go in platform-lab's Ansible Vault (key ~/.config/platform-lab/vault-pass)
  unless this repo gets its own.
- Ask before terraform destroy or anything that deletes VMs, volumes or cluster state.
- Commit or push only when the user asks.

## Ground rules
- Built from scratch on personal hardware.
- No configs, naming, hostnames, or architecture from any employer system.
- Models used: openly licensed; record source, license and SHA-256 for each.

## Host safety notes (carried over)
- NEVER touch enp3s0 / NetworkManager "Wired connection 2": it is the Raspberry
  Pi wake-on-LAN link. eno1 was the iMac cable.
- In Claude Code sessions run ansible with `</dev/null`.
- Lab SSH key is ~/.ssh/platform-lab_ecdsa (FIPS rejects ed25519).
- After a platform-lab rebuild the registry is empty: republish with `make image`.

## Session notes
Running notes for the user live in ./NOTES.txt (same style as
~/platform-project.txt: dated entries, numbered sections, what/why/result,
"NEXT:" and "STILL OPEN:" lines). Append to it at the end of each session.
NOTES.txt is gitignored (personal notes).

Standing preferences for the notes (user, 11/20):
- Every new tool gets links to its documentation and, where possible, videos,
  preferably from the vendor/project itself (official docs, project or CNCF
  channels). Check links resolve before adding; label non-vendor ones.
- When the user says they are done for the session: append the day's entry to
  NOTES.txt and email it with `~/scripts/sendme -s "<subject>" NOTES.txt [drafts...]`
  (short summary as the body, piped on stdin).

## Current status
11/21: public repo github.com/JGGHalfhide/local-airgap-LLM (MIT). Host
benchmarks done, model and runtime chosen, resource plan proposed. Next: the
platform-lab VM resize (docs/resource-plan.md), in-VM benchmark, tag v0.1.
Phase 3 (airgapped agents on Kubernetes) will be a separate repo.
See ROADMAP.md for the checklist.

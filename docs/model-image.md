# The model image

The model reaches the cluster as a signed OCI image in the platform
registry, mounted into the inference pod as a read-only image volume
([ADR 0003](adr/0003-model-as-oci-image-volume.md)).

## Publishing: `make model`

`make model [MODEL=<name>]` (`scripts/publish-model.sh`). `<name>` is a row of
[`benchmarks/candidates.tsv`](../benchmarks/candidates.tsv); the default is the
model chosen in ADR 0002.

| Step | Tool | Detail |
|---|---|---|
| Fetch and check | curl, sha256sum | `scripts/fetch-models.sh`: the GGUF at its pinned Hugging Face revision, SHA-256 checked; cached in `models/` (gitignored). |
| Build | `scripts/build-model-image.py` | OCI layout with one linux/amd64 manifest and one uncompressed layer holding the GGUF at its root: mode 0444, owner 0:0, mtime 0. The SHA-256 is checked again while the file is copied. Same file in, same digest out. |
| Push | crane | To `registry.platform-lab.internal/models/<name>:hf-<revision>`; pushed digest checked against the built one. |
| Sign and attest | cosign 3.1.3 | Signature on the manifest digest and a SLSA v1 provenance attestation (`slsaprovenance1`), platform-lab's lab key, no transparency log, tag-based layout: the same flags as platform-lab's `make image`. Both verified against `pki/cosign.pub`, and the attested upstream SHA-256 checked against the pin. Skipped if already valid. |

Registry login, the lab CA and the vault come from platform-lab's
`scripts/lib.sh`, so platform-lab must be checked out next to this repo.
Outputs (OCI layout, provenance, verification results) land in
`build/models/<name>/<digest>/` (gitignored).

No SBOM or Trivy scan: the image holds one data file and no packages or
executables. The provenance attestation records what it contains instead.

## Provenance

On the manifest as annotations (and as config labels), and in the signed
attestation:

| Field | Annotation | Qwen3.5 4B Q4_K_M |
|---|---|---|
| Source | `org.opencontainers.image.source` | https://huggingface.co/ggml-org/Qwen3.5-4B-GGUF |
| Revision | `org.opencontainers.image.revision` | `8d43d974efc48399cb58957ad49c8b26bf5a109c` |
| License | `org.opencontainers.image.licenses` | apache-2.0 |
| File | `io.github.jgghalfhide.local-airgap-llm.model.file` | `Qwen3.5-4B-Q4_K_M.gguf` |
| Upstream SHA-256 | `io.github.jgghalfhide.local-airgap-llm.model.sha256` | `217002fa8008ef4fc6d548353a3aebef055a96e6ff8e0e2c0953ef8254a89c14` |
| Quantization | `io.github.jgghalfhide.local-airgap-llm.model.quantization` | Q4_K_M |

The attestation also records this repo's commit and the exact download URL.
Kyverno does not check attestations at admission yet (kyverno/kyverno#17109,
see platform-lab's `docs/supply-chain.md`); `make model` verifies it at
publish time.

## Published

| Model | Reference | Size | Signed |
|---|---|---|---|
| qwen3.5-4b-q4km | `models/qwen3.5-4b-q4km:hf-8d43d974efc4@sha256:fb1e513b3791251de054a3f1682f9b5d58751664a1cdfbc451185abfb235bd61` | 2.8 GiB | yes, with provenance |

Pods reference it as
`127.0.0.1:30500/models/qwen3.5-4b-q4km@sha256:fb1e513b...` in
`spec.volumes[].image.reference`. A first run took 1 min 43 s on the build
host; a second run rebuilt the same digest and skipped push and signing.

## Admission test (11/22)

Full output: [`docs/scans/model-admission-test.txt`](scans/model-admission-test.txt).
Build cluster, platform-lab 49d69ea policies, namespace with restricted Pod
Security.

| Case | Result |
|---|---|
| Signed model, by digest | admitted; file mounted read-only, SHA-256 inside the pod equals the pin |
| Tampered model (same image, one annotation changed with `crane mutate`, so a new unsigned digest) | rejected by `verify-platform-images`: not signed with the platform-lab cosign key; `cosign verify`: no signatures found |

Changing the file itself has the same effect (any change gives a new
digest), and is caught earlier by the SHA-256 checks in `make model`.

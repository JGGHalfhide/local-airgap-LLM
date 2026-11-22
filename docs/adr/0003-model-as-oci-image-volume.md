# 0003. The model as an OCI image, mounted as an image volume

Status: accepted (Week 2, 11/21)

## Context

The model (Qwen3.5 4B Q4_K_M, one 2.8 GiB GGUF file, ADR 0002) has to reach
the inference pod the way every image does in platform-lab: stored in Zot,
pinned by digest, signed with the lab cosign key (no transparency log),
verified by Kyverno at admission, and carried to the airgapped cluster by
Zarf.

Options considered:

- **ORAS artifact** (the GGUF as a blob with its own artifact type). Clean
  for storage, but nothing in Kubernetes mounts it: an init container would
  pull it into an emptyDir or PVC on every start (2.8 GiB copied, a registry
  client in the pod, credentials or a pull path for it). Kyverno verifies
  image references in pod specs, not what an init container downloads, so
  the signature check would have to move into the init container.
- **OCI image wrapping the GGUF**, mounted as a Kubernetes image volume
  (`spec.volumes[].image`, stable since Kubernetes 1.36; RKE2 v1.36.5 ships
  containerd 2.3.4). The kubelet pulls it like any image, it is cached on
  the node, mounted read-only, and goes through the same tooling as the
  runtime image: crane, cosign, Zot, Zarf.
- **Model baked into the runtime image.** One artifact, but every model
  change rebuilds and rescans a runtime image 2.8 GiB larger, and the
  model and runtime could not be versioned or signed separately.

Tested 11/21 on the build cluster's policies (Kyverno 1.19.2, offline with
the Kyverno CLI against Zot):

- Kyverno's default image extractors cover containers only. An unsigned
  image volume, and one referenced by tag, passed the existing
  `verify-platform-images` policy. With `spec.volumes[].image.reference`
  added to the policy's own extractor, both were rejected and a signed one
  was admitted.
- `restrict-image-registries` and `disallow-latest-tag` had the same gap.
- platform-lab's `pod-security-restricted` rejected the `image` volume type,
  although Kubernetes 1.36's built-in Pod Security admission allows it in
  the restricted profile (`check_restrictedVolumes.go`).

## Decision

Package the model as an OCI image and mount it as an image volume.

- One linux/amd64 image, no base and no executables: a single layer holding
  the GGUF file (read-only for any UID), with a fixed timestamp and owner so
  the same upstream file always gives the same digest.
- Provenance as OCI annotations on the manifest (source repository and
  revision, file name, upstream SHA-256, license, quantization) and as a
  signed in-toto attestation next to the cosign signature, both made with
  the lab key in cosign's tag-based layout, like `make image`.
- The inference pod mounts it read-only by digest from
  `127.0.0.1:30500/...`; llama-server reads the file from the mount path.
- platform-lab's three image policies match image volumes as well as
  containers, and `pod-security-restricted` allows the `image` volume type
  (platform-lab change, 11/21).

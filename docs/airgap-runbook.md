# Airgap runbook

How the inference endpoint gets onto the disconnected cluster
(`airgap-rehearsal`, 192.168.122.20: a second VM on the build host, 8 vCPU /
16 GiB / 60 GiB, Rocky Linux 9, single-node RKE2). It reuses platform-lab's
airgap flow ([platform-lab docs/airgap-runbook.md](https://github.com/JGGHalfhide/platform-lab/blob/main/docs/airgap-runbook.md)):
everything is built and signed on the connected build host, copied over, and
installed with the node's egress blocked. Zarf is the deploy tool there; Argo
CD does not run on the airgapped cluster.

The two VMs do not run at the same time (16 GiB each on ~28 GiB usable;
docs/resource-plan.md): packages are built while build-node-01 runs, then it
is shut down and the airgap VM started.

## What goes over

| Package | Repo | Contents |
|---|---|---|
| platform, policies, sample-app, monitoring | platform-lab | Kyverno, the policy set (Zarf registry variant), sample app, Prometheus/Grafana/Loki/Alloy |
| keda | platform-lab (`zarf/keda`) | KEDA 2.21.1 + HTTP add-on 0.16.0 from `gitops/keda`; six images with `.sig`/`.att` |
| llm | this repo (`zarf/llm`) | `deploy/` (Deployment, Services, NetworkPolicies, ServiceMonitor, ScaledObject, InterceptorRoute), dashboard ConfigMap; llama.cpp runtime and model images with `.sig`/`.att` |
| llm-smoke | this repo (`zarf/llm-smoke`) | alpine (signed) for the smoke test's client pods; the unsigned model copy the smoke test expects to be rejected |

Every package is signed with the lab cosign key and deployed with
`--verify=always` against `pki/cosign.pub`. Inside the packages, every image
the cluster runs is pinned by digest and carries its own cosign signature,
which Kyverno checks at admission against the same key.

Changes made in platform-lab for this (Week 5):

- `zarf/policies`: the airgap variant of `verify-platform-images` rebuilt the
  image list from containers only, dropping the image volumes added in
  250acd1, so an unsigned model would have been admitted on the airgapped
  cluster. It now includes image volumes (policies package 0.7.0). The Zarf
  agent (v0.89.0) rewrites image volume references as it does container
  images.
- `zarf/keda`: new. Zarf installs manifests as Helm releases, and Helm
  rejects a release with a custom resource whose CRD does not exist yet, so
  the interceptor's ScaledObject is a second component.
- zarf role: `keda` in the package list; `zarf_extra_packages` for packages
  built in another repo; Zarf registry volume 20Gi (was 10Gi) for the
  2.8 GiB model.
- `scripts/airgap-packages.sh`: `ZARF_SRC` to build another repo's packages
  with the same signing.

## Steps

### 1. Build the packages (build host, build-node-01 running)

```bash
make smoke                                  # on the build cluster: (re)creates the unsigned model copy
(cd ../platform-lab && make airgap-packages) # platform, policies, sample-app, monitoring, keda
make airgap-packages                         # llm, llm-smoke -> build/airgap/packages/
```

`make airgap-packages` here runs platform-lab's `scripts/airgap-packages.sh`
with `ZARF_SRC=zarf/`: images (and their signatures) are pulled from the
build cluster's registry over TLS, the package is signed with the lab key
(password from Vault) and checked against `pki/cosign.pub`, and a
`SHA256SUMS` is written.

### 2. Switch VMs

```bash
virsh -c qemu:///system shutdown build-node-01
virsh -c qemu:///system start airgap-rehearsal
```

### 3. RKE2, then block egress (platform-lab)

```bash
cd ../platform-lab
make airgap-stage        # once: RKE2 RPMs and images, Zarf CLI and init package
make airgap-up           # base, hardening, RKE2 from staged artifacts; ./kubeconfig-airgap
make airgap-disconnect   # firewalld egress policies; fails unless curl to the internet fails
```

### 4. Deploy (egress blocked)

```bash
make airgap-deploy
```

Runs platform-lab's `ansible/airgap-deploy.yml` (zarf role) with this repo's
packages as `zarf_extra_packages`: `zarf init`, then platform, policies,
sample-app, monitoring, keda, llm, llm-smoke, each verified against the lab
key. Packages already deployed with the same checksum and build time are
skipped, so a second run reports `changed=0`.

### 5. Verify

```bash
export KUBECONFIG=../platform-lab/kubeconfig-airgap
make smoke                     # airgap mode: detected from the zarf namespace
scripts/cold-start.sh 3
kubectl get events -A | grep -c -E 'ErrImagePull|ImagePullBackOff'   # 0
```

### Test namespaces and Zarf's pull Secret

The Zarf agent rewrites every pod to its registry and adds
`imagePullSecrets: [private-registry]`, but Zarf creates that Secret only in
the namespaces that exist when its packages deploy. The namespaces that
`smoke-test.sh` and `cold-start.sh` create later get a copy of the one in
`llm` (`scripts/zarf-pull-secret.sh`; no-op on the build cluster). Without
it their pods fail with `authorization failed: no basic auth credentials`.

## Results (11/22, airgap-rehearsal, egress blocked)

| Step | Result |
|---|---|
| Packages built and signed | keda 730 MB (22 s), llm 3.5 GB (53 s), llm-smoke 2.9 GB (22 s); each checked against `pki/cosign.pub` |
| `make airgap-up` | 4m06s to a Ready node (RKE2 v1.36.5, containerd 2.3.4); repeat 78 s, `changed=0` |
| `make airgap-disconnect` | `curl https://rpm.rancher.io`: `No route to host` |
| `make airgap-deploy` | 7m48s: init, platform, policies, sample-app, monitoring, keda, llm, llm-smoke, all components `Succeeded`, each verified against the lab key |
| `make airgap-deploy` (repeat) | 33 s, `changed=0` |
| Image pull errors | 0 |
| Images in the pod | `127.0.0.1:31999/ggml-org/llama.cpp@sha256:e3032ea0...` and image volume `127.0.0.1:31999/models/qwen3.5-4b-q4km@sha256:fb1e513b...`: the signed digests, rewritten by the Zarf agent, admitted after signature verification |
| `make smoke` | 7/7 pass: "Paris" in 16.0 s from zero replicas (14.5 tok/s generation); unlabelled pod and interceptor bypass blocked; no egress from the inference pod (internet, Zarf registry, DNS) |
| Unsigned model | rejected: `Policy verify-platform-images failed: image is not signed with the platform-lab cosign key` (image present in the Zarf registry) |
| Cold start (`scripts/cold-start.sh 3`) | first byte 5.9 / 6.8 / 6.8 s, short reply 6.3 / 7.2 / 7.2 s; pod ready +6-7 s; warm 0.2 s. Build cluster: 6.2-6.4 s |
| Dashboard | Prometheus target `llm` up; `llamacpp:tokens_predicted_total` 134 after the smoke test; Grafana sidecar loaded the dashboard into the `local-airgap-llm` folder |

Found during the run and fixed:

- The airgap policy variant dropped image volumes (above, platform-lab).
- Helm rejects KEDA's interceptor ScaledObject in the same release as the
  CRDs (`zarf/keda` split into two components).
- Test namespaces need Zarf's pull Secret (above).
- Ansible's `.known_hosts` (platform-lab) held only the VM's ed25519 key;
  the nodes are reached over ECDSA (FIPS), so SSH reported a changed host.
  The ECDSA key was read on the VM over the trusted ed25519 key and added.
  A `make inventory` rebuild starts from an empty file and does not hit this.
- The `llm-smoke` package carries the model's layers again (packages do not
  share blobs); the Zarf registry stores them once.

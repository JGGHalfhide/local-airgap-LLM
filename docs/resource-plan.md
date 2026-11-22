# Resource plan: two VMs on one host

Status: applied (Week 1, 11/21). Both VMs resized and running.

## Host

GMKtec K8 Plus: Ryzen 7 8845HS (8 cores / 16 threads, AVX-512, 16 MiB L3),
28.2 GiB RAM visible to Linux (the rest is reserved for the iGPU), 445 GB
free disk. VMs use libvirt `host-passthrough` CPU mode, so llama.cpp gets
the same AVX-512 kernels inside the VMs as on the host.

## What the inference pod needs

From benchmarks/README.md (Qwen3.5 4B Q4_K_M, ADR 0002, llama-server
settings from ADR 0001):

| Setting | Peak memory |
|---|---|
| 8k context, 4 slots | 3.9 GiB |
| 32k context, 4 slots (unified KV) | 4.6 GiB |

Planned pod sizing: `-c 32768`, 4 slots sharing the KV cache, memory
request 5 GiB, limit 6 GiB. CPU: generation speed is limited by memory
bandwidth (4 threads is within 3% of 8), prompt processing scales with
cores (about 1.8x from 4 to 8 threads); 16 threads is no faster than 8.
Inside the 8-vCPU VM, with the platform's pods taking about 1.3 cores,
6 threads is best for generation and 8 for prompt processing
(benchmarks/README.md), so the pod runs `-t 6 -tb 8` and requests 6 CPUs.

Disk per cluster for the model: about 2.8 GiB in the registry (Zot or the
Zarf registry) plus about 2.8 GiB in the node's containerd store or volume.

## Plan

| | vCPU | RAM | Disk (thin qcow2) |
|---|---|---|---|
| build-node-01 (was 4 / 12 GiB / 40 GiB) | 8 | 12 GiB | 40 GiB (unchanged) |
| airgap target (rehearsal VM, was 4 / 8 GiB / 60 GiB) | 8 | 12 GiB | 60 GiB |
| Host left when both run | | about 4 GiB | |

- vCPUs are overcommitted (16 vCPUs on 16 host threads). Acceptable: only
  one cluster is load-tested at a time, and both VMs running inference at
  once would contend for the same memory bandwidth anyway.
- 12 GiB per VM = platform baseline + the inference pod (up to 6 GiB
  limit) + KEDA. Measured 11/21 on build-node-01 with the full stack (Argo
  CD, Zot, Kyverno, kube-prometheus-stack, Loki, Alloy, Velero, sample app):
  4 GiB used, 6 GiB available of 11 GiB visible to the guest. The pod fits
  with little slack; KEDA and the dashboards are measured in Week 4.
- Both VMs only need to run together while moving packages; Zarf packages
  are created on the host. If memory is tight, the build VM can be shut
  down while the airgap VM is tested.

## Changes made in platform-lab (11/21)

- `vcpus = 8`; `rehearsal_vcpus = 8`, `rehearsal_memory_mib = 12288`;
  descriptions and the module comment no longer refer to the iMac. The
  module keeps its `airgap_rehearsal` name (a rename needs `moved` blocks
  and Makefile/inventory changes for no functional gain).
- build-node-01's disk stays at 40 GiB: the libvirt provider (0.9.x)
  cannot resize a volume in place ("Storage volumes cannot be updated. All
  changes require replacement."), so growing it would mean rebuilding the
  build node. 15.8 GiB of 40 was allocated on 11/21; the model adds about
  6 GiB (registry blob plus the node's copy). Revisit if disk gets tight.

## As deployed (Week 3, 11/22)

The pod runs smaller than planned above: `--ctx-size 8192` (4 slots sharing
the KV cache), requests 4 CPU / 4 GiB, limit 5 GiB (peak measured: 3.9 GiB).

- CPU: the platform's pods already request 2.75 CPU of 8 allocatable, so a
  6-CPU request cannot be scheduled. The request is 4; there is no CPU
  limit, so the 6/8 threads still get the idle cores.
- Memory: with the server running, requests are 10.2 of 11.4 GiB
  allocatable. The planned 32k context (5 GiB request) would leave about
  0.2 GiB for KEDA and the Week 4 dashboards.
- 32k context for Phase 3 agents: revisit after Week 4 has measured KEDA's
  footprint (needs a bigger VM or a smaller platform footprint).


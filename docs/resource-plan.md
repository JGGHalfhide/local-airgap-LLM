# Resource plan: two VMs on one host

Status: proposed (Week 1, 11/21). Platform baseline memory to be confirmed
when the VMs are up; this page is updated with the measured numbers.

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

Disk per cluster for the model: about 2.8 GiB in the registry (Zot or the
Zarf registry) plus about 2.8 GiB in the node's containerd store or volume.

## Plan

| | vCPU | RAM | Disk (thin qcow2) |
|---|---|---|---|
| build-node-01 (today 4 / 12 GiB / 40 GiB) | 8 | 12 GiB | 60 GiB |
| airgap target (rehearsal VM today 4 / 8 GiB / 60 GiB) | 8 | 12 GiB | 60 GiB |
| Host left when both run | | about 4 GiB | |

- vCPUs are overcommitted (16 vCPUs on 16 host threads). Acceptable: only
  one cluster is load-tested at a time, and both VMs running inference at
  once would contend for the same memory bandwidth anyway.
- 12 GiB per VM = platform baseline (on the 8 GiB iMac: 3.7 GiB in use
  before the monitoring stack; to be measured with the full stack) + the
  inference pod (up to 6 GiB limit) + KEDA.
- Both VMs only need to run together while moving packages; Zarf packages
  are created on the host. If memory is tight, the build VM can be shut
  down while the airgap VM is tested.

## Changes needed in platform-lab

- `rehearsal_vcpus = 8`, `rehearsal_memory_mib = 12288`; the descriptions
  stop referring to the iMac. Possibly rename the module from rehearsal to
  airgap target.
- `vcpus = 8`; `disk_size_gib = 60` for build-node-01.
- Check before applying whether the libvirt provider changes vCPU/RAM in
  place (VM restart) or replaces the domain, and whether growing the disk
  replaces the volume. Anything that destroys a VM or volume is confirmed
  first.

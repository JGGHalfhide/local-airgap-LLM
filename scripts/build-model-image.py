#!/usr/bin/env python3
"""Build the OCI image layout for one GGUF model (ADR 0003).

    scripts/build-model-image.py <name> <out-dir>

<name> is a row of benchmarks/candidates.tsv. The GGUF must already be in
models/ (scripts/fetch-models.sh). Writes an OCI image layout to <out-dir>
holding one linux/amd64 image manifest (no index wrapper), and prints the
manifest digest.

The image has no base and no executables: one uncompressed tar layer with
the GGUF file at its root, read-only for every UID (mode 0444, owner 0:0,
mtime 0), so the same upstream file always gives the same digest. The
file's SHA-256 is checked again while it is copied into the layer.

Provenance travels as manifest annotations (and the same values as config
labels): source repository, revision, file, upstream SHA-256, license and
quantization. publish-model.sh adds a signed attestation with the same data.
"""
import hashlib
import json
import pathlib
import re
import sys
import tarfile

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PREFIX = "io.github.jgghalfhide.local-airgap-llm.model"
CHUNK = 16 << 20


def candidate(name):
    for line in (REPO_ROOT / "benchmarks/candidates.tsv").read_text().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        row = dict(zip(["name", "repo", "revision", "file", "sha256", "license"],
                       line.split("\t")))
        if row["name"] == name:
            return row
    sys.exit(f"{name}: not in benchmarks/candidates.tsv")


def quantization(filename):
    m = re.search(r"(I?Q\d_K_[SML]|I?Q\d_K|Q\d_\d|BF16|F16|F32)", filename, re.I)
    return m.group(1).upper() if m else "unknown"


class HashingWriter:
    """File writer that also hashes and counts what passes through."""

    def __init__(self, f):
        self.f, self.sha, self.size = f, hashlib.sha256(), 0

    def write(self, b):
        self.sha.update(b)
        self.size += len(b)
        return self.f.write(b)


class HashingReader:
    def __init__(self, f):
        self.f, self.sha = f, hashlib.sha256()

    def read(self, n=-1):
        b = self.f.read(n)
        self.sha.update(b)
        return b


def write_blob(blobs, data):
    digest = hashlib.sha256(data).hexdigest()
    (blobs / digest).write_bytes(data)
    return {"digest": f"sha256:{digest}", "size": len(data)}


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    row = candidate(sys.argv[1])
    out = pathlib.Path(sys.argv[2])
    gguf = REPO_ROOT / "models" / row["file"]
    if not gguf.is_file():
        sys.exit(f"{gguf}: missing; run scripts/fetch-models.sh {row['name']}")

    blobs = out / "blobs" / "sha256"
    blobs.mkdir(parents=True, exist_ok=True)

    # Layer: a single regular file, fixed metadata.
    tmp = blobs / "layer.tmp"
    info = tarfile.TarInfo(row["file"])
    info.size = gguf.stat().st_size
    info.mode, info.mtime = 0o444, 0
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    with open(tmp, "wb") as f, open(gguf, "rb") as src:
        w = HashingWriter(f)
        reader = HashingReader(src)
        with tarfile.open(fileobj=w, mode="w|", format=tarfile.GNU_FORMAT,
                          bufsize=CHUNK) as tar:
            tar.addfile(info, reader)
    if reader.sha.hexdigest() != row["sha256"]:
        tmp.unlink()
        sys.exit(f"{gguf}: SHA-256 {reader.sha.hexdigest()} != pinned {row['sha256']}")
    layer_digest = w.sha.hexdigest()
    tmp.rename(blobs / layer_digest)

    source = f"https://huggingface.co/{row['repo']}"
    meta = {
        "org.opencontainers.image.title": row["name"],
        "org.opencontainers.image.description":
            f"{row['file']} from {row['repo']} as an image volume (no executables)",
        "org.opencontainers.image.source": source,
        "org.opencontainers.image.revision": row["revision"],
        "org.opencontainers.image.licenses": row["license"],
        "org.opencontainers.image.url": f"{source}/blob/{row['revision']}/{row['file']}",
        f"{PREFIX}.file": row["file"],
        f"{PREFIX}.sha256": row["sha256"],
        f"{PREFIX}.quantization": quantization(row["file"]),
        f"{PREFIX}.format": "gguf",
    }
    config = {
        "architecture": "amd64",
        "os": "linux",
        "config": {"Labels": meta},
        "rootfs": {"type": "layers", "diff_ids": [f"sha256:{layer_digest}"]},
        "history": [{"created_by": "local-airgap-LLM scripts/build-model-image.py"}],
    }
    config_desc = write_blob(blobs, canonical(config))
    manifest = {
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "config": {"mediaType": "application/vnd.oci.image.config.v1+json", **config_desc},
        "layers": [{
            "mediaType": "application/vnd.oci.image.layer.v1.tar",
            "digest": f"sha256:{layer_digest}",
            "size": w.size,
            "annotations": {"org.opencontainers.image.title": row["file"]},
        }],
        "annotations": meta,
    }
    manifest_desc = write_blob(blobs, canonical(manifest))
    (out / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}')
    (out / "index.json").write_bytes(canonical({
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.index.v1+json",
        "manifests": [{
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            **manifest_desc,
            "platform": {"architecture": "amd64", "os": "linux"},
        }],
    }))
    print(manifest_desc["digest"])


if __name__ == "__main__":
    main()

#!/usr/bin/env bash
# Package a GGUF model as a signed OCI image in the platform registry
# (ADR 0003).
#
#   scripts/publish-model.sh <name>       (or: make model MODEL=<name>)
#
# <name> is a row of benchmarks/candidates.tsv (default model in the
# Makefile: the one chosen in ADR 0002).
#
# 1. Fetch the GGUF at its pinned revision and check its SHA-256
#    (scripts/fetch-models.sh; cached in models/).
# 2. Build the image as an OCI layout (scripts/build-model-image.py): one
#    uncompressed layer with the file, provenance as annotations. Same file
#    in, same digest out.
# 3. Push to $REGISTRY/models/<name>:<tag> with crane and check the pushed
#    digest equals the built one.
# 4. cosign: sign the manifest digest and attach a SLSA v1 provenance
#    attestation (source URL, revision, upstream SHA-256, license,
#    quantization, this repo's commit), with platform-lab's lab key, no
#    transparency log, tag-based layout: the same flags as platform-lab's
#    publish-image.sh. Then verify both against pki/cosign.pub. Skipped if
#    both already verify.
#
# No SBOM or Trivy scan: the image holds one data file and no packages or
# executables, so there is nothing for them to find. The provenance
# attestation records what the image contains instead.
#
# Registry login, lab CA and the vault come from platform-lab's
# scripts/lib.sh ($PLATFORM_LAB, default ../platform-lab next to this repo).
# Outputs land in build/models/<name>/<digest>/ (gitignored); the last line
# printed is the pushed reference, pinned by digest.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PLATFORM_LAB="${PLATFORM_LAB:-$(cd "$ROOT/../platform-lab" && pwd)}"
source "$PLATFORM_LAB/scripts/lib.sh"   # sets REPO_ROOT to platform-lab
COSIGN_KEY="${COSIGN_KEY:-$HOME/.config/platform-lab/cosign.key}"
SIGN="${SIGN:-1}"

name="${1:?usage: $0 <name from benchmarks/candidates.tsv>}"
row="$(grep -v '^#' "$ROOT/benchmarks/candidates.tsv" | awk -F'\t' -v n="$name" '$1 == n')"
[[ -n "$row" ]] || { echo "$name: not in benchmarks/candidates.tsv" >&2; exit 1; }
IFS=$'\t' read -r _ hf_repo revision file sha256 license <<<"$row"
repo="models/$name"
tag="hf-${revision:0:12}"

echo "==> Fetching $name ($hf_repo@${revision:0:8})"
"$ROOT/scripts/fetch-models.sh" "$name"

echo "==> Building the OCI layout"
work="$ROOT/build/models/$name/layout"
rm -rf "$work"
digest="$(python3 "$ROOT/scripts/build-model-image.py" "$name" "$work")"
out="$ROOT/build/models/$name/${digest#sha256:}"
rm -rf "$out"
mkdir -p "$out"
mv "$work" "$out/oci"
echo "    $digest"

trap registry_env_cleanup EXIT
registry_env
target="$REGISTRY/$repo@$digest"

if [[ "$(crane digest "$REGISTRY/$repo:$tag" 2>/dev/null || true)" == "$digest" ]]; then
  echo "==> Already in the registry: $REGISTRY/$repo:$tag"
else
  echo "==> Pushing to $REGISTRY/$repo:$tag"
  crane push "$out/oci" "$REGISTRY/$repo:$tag" >/dev/null
  pushed="$(crane digest "$REGISTRY/$repo:$tag")"
  if [[ "$pushed" != "$digest" ]]; then
    echo "Pushed digest $pushed differs from the built $digest" >&2
    exit 1
  fi
fi

# SLSA v1 provenance (https://slsa.dev/spec/v1.0/provenance).
commit="$(git -C "$ROOT" rev-parse HEAD)"
[[ -z "$(git -C "$ROOT" status --porcelain -- scripts benchmarks/candidates.tsv)" ]] ||
  commit="$commit-dirty"
python3 - "$out/provenance.json" "$name" "$hf_repo" "$revision" "$file" "$sha256" \
  "$license" "$commit" "$digest" "$out/oci/blobs/sha256/${digest#sha256:}" <<'EOF'
import json, sys, datetime
path, name, repo, rev, file, sha, lic, commit, digest, manifest = sys.argv[1:]
labels = json.load(open(manifest))["annotations"]
now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
url = f"https://huggingface.co/{repo}/resolve/{rev}/{file}"
json.dump({
    "buildDefinition": {
        "buildType": "https://github.com/JGGHalfhide/local-airgap-LLM/blob/main/docs/adr/0003-model-as-oci-image-volume.md",
        "externalParameters": {
            "model": name,
            "source": {"repository": f"https://huggingface.co/{repo}", "revision": rev,
                       "file": file, "sha256": sha},
            "license": lic,
            "quantization": labels["io.github.jgghalfhide.local-airgap-llm.model.quantization"],
        },
        "internalParameters": {"layer": {"mediaType": "application/vnd.oci.image.layer.v1.tar",
                                         "mode": "0444", "uid": 0, "gid": 0, "mtime": 0}},
        "resolvedDependencies": [
            {"uri": url, "name": file, "digest": {"sha256": sha}},
            {"uri": "git+https://github.com/JGGHalfhide/local-airgap-LLM", "digest": {"gitCommit": commit}},
        ],
    },
    "runDetails": {
        "builder": {"id": "https://github.com/JGGHalfhide/local-airgap-LLM/blob/main/scripts/publish-model.sh"},
        "metadata": {"invocationId": digest, "finishedOn": now},
    },
}, open(path, "w"), indent=2)
EOF

verify_flags=(--key "$REPO_ROOT/pki/cosign.pub" --insecure-ignore-tlog --new-bundle-format=false)
verified() {
  cosign verify "${verify_flags[@]}" "$target" >"$out/cosign-verify.json" 2>/dev/null &&
    cosign verify-attestation "${verify_flags[@]}" --type slsaprovenance1 "$target" \
      >"$out/cosign-verify-attestation.json" 2>/dev/null
}
if [[ "$SIGN" == 1 ]] && verified; then
  echo "==> Already signed with a provenance attestation (pki/cosign.pub); skipping cosign"
elif [[ "$SIGN" == 1 ]]; then
  echo "==> Signing and attesting provenance (cosign)"
  COSIGN_PASSWORD="$(vault_get vault_cosign_password)"
  export COSIGN_PASSWORD
  cosign_flags=(--yes --key "$COSIGN_KEY" --new-bundle-format=false
    --use-signing-config=false --tlog-upload=false)
  cosign sign "${cosign_flags[@]}" "$target" 2>"$out/cosign-sign.log" ||
    { cat "$out/cosign-sign.log" >&2; exit 1; }
  cosign attest "${cosign_flags[@]}" --type slsaprovenance1 \
    --predicate "$out/provenance.json" "$target" 2>"$out/cosign-attest.log" ||
    { cat "$out/cosign-attest.log" >&2; exit 1; }
  unset COSIGN_PASSWORD
  verified || { echo "Signature or provenance verification failed for $target" >&2; exit 1; }
  echo "    signature and provenance attestation verified"
fi

# The attested upstream SHA-256 must match the pinned one.
if [[ "$SIGN" == 1 ]]; then
  python3 - "$out/cosign-verify-attestation.json" "$sha256" <<'EOF'
import base64, json, sys
shas = set()
for line in open(sys.argv[1]):
    if line.strip():
        stmt = json.loads(base64.b64decode(json.loads(line)["payload"]))
        shas.add(stmt["predicate"]["buildDefinition"]["externalParameters"]["source"]["sha256"])
if shas != {sys.argv[2]}:
    sys.exit(f"attested SHA-256 {shas} != pinned {sys.argv[2]}")
print(f"    attested upstream SHA-256 matches the pin")
EOF
fi

printf '%s\n' "127.0.0.1:30500/$repo@$digest" >"$out/pod-reference"
printf '%s\n' "$REGISTRY/$repo:$tag@$digest" | tee "$out/pushed"

#!/usr/bin/env bash
# Download the GGUF files listed in benchmarks/candidates.tsv into models/
# (gitignored) at their pinned revision, and verify each SHA-256.
# Usage: scripts/fetch-models.sh [name ...]   (default: all)
set -euo pipefail

repo_root=$(cd "$(dirname "$0")/.." && pwd)
dest="$repo_root/models"
mkdir -p "$dest"

grep -v '^#' "$repo_root/benchmarks/candidates.tsv" |
while IFS=$'\t' read -r name repo rev file sha license; do
  [[ -n $name ]] || continue
  if (($#)) && [[ " $* " != *" $name "* ]]; then continue; fi
  out="$dest/$file"
  if [[ -f $out ]] && echo "$sha  $out" | sha256sum -c --status -; then
    echo "ok (cached)  $name"; continue
  fi
  echo "fetching     $name ($repo@${rev:0:8}, $license)"
  curl -fL --retry 3 -C - -o "$out.part" "https://huggingface.co/$repo/resolve/$rev/$file"
  echo "$sha  $out.part" | sha256sum -c --quiet -
  mv "$out.part" "$out"
  echo "ok           $name"
done

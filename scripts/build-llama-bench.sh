#!/usr/bin/env bash
# Build llama.cpp (CPU only) at a pinned commit for benchmarking on the host
# or inside a VM. Output: build/llama.cpp/build/bin/{llama-bench,llama-cli,llama-server}
# GGML_NATIVE=ON tunes for the CPU it is built on (AVX-512 on the 8845HS);
# the runtime image for the cluster is built separately in Week 2.
set -euo pipefail

LLAMA_CPP_VERSION=v1.0.0
LLAMA_CPP_COMMIT=b1b770d90d94cad9e9ea5b993bdbc383a522fb28

repo_root=$(cd "$(dirname "$0")/.." && pwd)
src="$repo_root/build/llama.cpp"

if [[ ! -d $src/.git ]]; then
  git clone --filter=blob:none https://github.com/ggml-org/llama.cpp "$src"
fi
git -C "$src" fetch --quiet origin "$LLAMA_CPP_COMMIT"
git -C "$src" -c advice.detachedHead=false checkout --quiet "$LLAMA_CPP_COMMIT"
[[ $(git -C "$src" rev-parse HEAD) == "$LLAMA_CPP_COMMIT" ]]

cmake -S "$src" -B "$src/build" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DGGML_NATIVE=ON \
  -DLLAMA_CURL=OFF \
  -DLLAMA_BUILD_TESTS=OFF
cmake --build "$src/build" --target llama-bench llama-cli llama-server -j"$(nproc)"

echo "built llama.cpp $LLAMA_CPP_VERSION ($LLAMA_CPP_COMMIT) in $src/build/bin"

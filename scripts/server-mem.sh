#!/usr/bin/env bash
# Peak memory of llama-server for one model under several settings, after one
# ~3k-token chat request. Reads VmHWM/RssAnon/RssFile from /proc.
# Usage: scripts/server-mem.sh models/<file>.gguf
set -euo pipefail

repo_root=$(cd "$(dirname "$0")/.." && pwd)
bin="$repo_root/build/llama.cpp/build/bin/llama-server"
model=${1:?model path}
port=18081
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

python3 -c 'import json; print(json.dumps({"messages": [{"role": "user", "content": "summarise: " + "the cluster node is healthy and pods are running. " * 300}], "max_tokens": 32, "chat_template_kwargs": {"enable_thinking": False}}))' > "$work/req.json"

measure() {
  local label=$1; shift
  "$bin" -m "$model" -t 8 --host 127.0.0.1 --port $port "$@" > "$work/$label.log" 2>&1 &
  local pid=$!
  for _ in $(seq 240); do
    curl -sf "localhost:$port/health" > /dev/null && break
    kill -0 $pid 2> /dev/null || { echo "$label: server exited: $(tail -1 "$work/$label.log")"; return 1; }
    sleep 0.5
  done
  curl -sf -m 300 "localhost:$port/v1/chat/completions" -H 'Content-Type: application/json' -d @"$work/req.json" -o /dev/null
  read -r hwm anon file < <(awk '/VmHWM/{h=$2} /RssAnon/{a=$2} /RssFile/{f=$2} END{print h, a, f}' /proc/$pid/status)
  printf "%-28s peak %5d MiB  (anon %5d, file-backed %5d)\n" "$label" $((hwm / 1024)) $((anon / 1024)) $((file / 1024))
  kill $pid; wait $pid 2> /dev/null || true
}

echo "$(basename "$model"), llama-server -t 8, default slots (4, unified KV)"
measure "mmap, ctx 8k"               -c 8192
measure "no mmap, ctx 8k"            -c 8192 -lm none
measure "no mmap, no repack, ctx 8k" -c 8192 -lm none -nr
measure "no mmap, ctx 32k"           -c 32768 -lm none
measure "no mmap, ctx 32k, 1 slot"   -c 32768 -lm none -np 1

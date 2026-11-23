#!/usr/bin/env python3
"""Load test of the inference endpoint (Week 6).

Sends streamed chat completions at fixed concurrency levels (default
1, 2, 4, 8) through the KEDA interceptor, the same path in-cluster clients
use, and reports per level:
  - requests, errors,
  - aggregate output tokens/s (all completion tokens / wall time),
  - per-request generation tokens/s (after the first token),
  - time to first token (TTFT) and end-to-end latency, p50 / p95,
  - CPU package energy from RAPL (optional, --rapl; needs sudo), and from
    it joules per output token.

Reaches the interceptor with `kubectl port-forward` from this host (needs
KUBECONFIG), sending Host: llm-api.llm.svc like an in-cluster client.
Port-forward adds a hop that in-cluster clients do not have; at ~15
tokens/s it is noise (well under a millisecond per chunk).

Every prompt starts with a unique request id, so no request reuses another's
cached prompt prefix: each one pays full prompt processing. Thinking is off
(chat_template_kwargs), temperature 0, max_tokens fixed: every request
generates the same number of tokens unless the model stops early.

A warm-up request runs first, so a cold start (docs/scaling.md) is not in
the numbers.

Usage:
  KUBECONFIG=... scripts/load-test.py [--levels 1,2,4,8] [--requests 16]
      [--max-tokens 128] [--rapl] [--idle 60] [--out build/load-test.json]
Stdlib only.
"""
import argparse
import http.client
import json
import math
import os
import socket
import statistics
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

HOST_HEADER = "llm-api.llm.svc"
MODEL = "qwen3.5-4b"
RAPL = "/sys/class/powercap/intel-rapl:0"

# About 230 tokens of shared context, then a question. Written for this test.
CONTEXT = (
    "You are the on-call assistant for a small Kubernetes platform that runs "
    "on one mini PC. The platform has a build cluster and an airgapped "
    "cluster. Images and models are stored in a private OCI registry, signed "
    "with a lab key, and verified at admission by a policy engine. Workloads "
    "run under the restricted Pod Security standard with default-deny "
    "network policies. An HTTP interceptor scales the inference server to "
    "zero after five minutes without requests and holds new requests while "
    "it starts again, which takes about six seconds. Metrics are scraped by "
    "Prometheus and shown in Grafana; logs go to Loki. Backups of cluster "
    "state are taken with Velero and etcd snapshots. The airgapped cluster "
    "receives everything as signed packages copied from the build host and "
    "has no route to the internet. Answer the operator's question in plain "
    "prose, in a few sentences, without lists or headings."
)
QUESTIONS = [
    "Why might the first request of the morning be slower than the rest?",
    "What should I check first if a new pod is rejected at admission?",
    "How would you confirm that the airgapped cluster really has no egress?",
    "What is the risk of running the inference server without a memory limit?",
    "Why sign the model as well as the container images?",
    "What would you look at if tokens per second dropped by half overnight?",
    "How do the etcd snapshots and Velero backups differ in what they restore?",
    "Why does the interceptor need to see every request to the server?",
]


def percentile(values, p):
    """Nearest-rank percentile (p in 0-100) of a non-empty list."""
    s = sorted(values)
    k = max(0, math.ceil(p / 100 * len(s)) - 1)
    return s[k]


def read_energy_uj():
    out = subprocess.run(["sudo", "-n", "cat", f"{RAPL}/energy_uj",
                          f"{RAPL}/max_energy_range_uj"],
                         check=True, capture_output=True, text=True).stdout
    energy, wrap = (int(x) for x in out.split())
    return energy, wrap


class Energy:
    """CPU package energy over an interval, from RAPL (host-wide)."""

    def __init__(self, enabled):
        self.enabled = enabled

    def start(self):
        if self.enabled:
            self.e0, self.wrap = read_energy_uj()
            self.t0 = time.monotonic()

    def stop(self):
        """Returns (joules, seconds) or (None, None)."""
        if not self.enabled:
            return None, None
        e1, _ = read_energy_uj()
        delta = e1 - self.e0 if e1 >= self.e0 else e1 + self.wrap - self.e0
        return delta / 1e6, time.monotonic() - self.t0


def one_request(host, port, req_id, max_tokens, timeout):
    """One streamed chat completion. Returns a dict of timings or an error."""
    question = QUESTIONS[req_id % len(QUESTIONS)]
    body = json.dumps({
        "model": MODEL,
        "stream": True,
        "stream_options": {"include_usage": True},
        "max_tokens": max_tokens,
        "temperature": 0,
        "chat_template_kwargs": {"enable_thinking": False},
        "messages": [
            {"role": "system", "content": f"Request {req_id}. {CONTEXT}"},
            {"role": "user", "content": question},
        ],
    })
    t0 = time.monotonic()
    ttft = None
    deltas = 0
    usage = None
    done = False
    try:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        conn.request("POST", "/v1/chat/completions", body=body, headers={
            "Host": HOST_HEADER, "Content-Type": "application/json"})
        resp = conn.getresponse()
        if resp.status != 200:
            return {"error": f"HTTP {resp.status}: {resp.read(200)!r}"}
        for raw in resp:
            line = raw.strip()
            if not line.startswith(b"data:"):
                continue
            data = line[5:].strip()
            if data == b"[DONE]":
                done = True
                break
            chunk = json.loads(data)
            if chunk.get("usage"):
                usage = chunk["usage"]
            for choice in chunk.get("choices", []):
                if choice.get("delta", {}).get("content"):
                    if ttft is None:
                        ttft = time.monotonic() - t0
                    deltas += 1
        conn.close()
    except (OSError, http.client.HTTPException, json.JSONDecodeError) as e:
        return {"error": f"{type(e).__name__}: {e}"}
    total = time.monotonic() - t0
    # A stream that ends without [DONE] and the usage chunk was cut off.
    if not done or usage is None or ttft is None:
        return {"error": f"incomplete stream: done={done} usage={bool(usage)} "
                         f"deltas={deltas} after {total:.1f} s"}
    tokens = usage["completion_tokens"]
    gen_time = total - ttft
    return {
        "ttft": ttft,
        "latency": total,
        "completion_tokens": tokens,
        "prompt_tokens": usage["prompt_tokens"],
        "gen_tps": (tokens - 1) / gen_time if tokens > 1 and gen_time > 0 else None,
    }


def run_level(host, port, concurrency, n_requests, max_tokens, timeout,
              energy, first_id):
    energy.start()
    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        results = list(pool.map(
            lambda i: one_request(host, port, first_id + i, max_tokens, timeout),
            range(n_requests)))
    wall = time.monotonic() - t0
    joules, _ = energy.stop()
    ok = [r for r in results if "error" not in r]
    errors = [r["error"] for r in results if "error" in r]
    out_tokens = sum(r["completion_tokens"] for r in ok)
    level = {
        "concurrency": concurrency,
        "requests": n_requests,
        "errors": len(errors),
        "error_samples": errors[:3],
        "wall_s": round(wall, 2),
        "output_tokens": out_tokens,
        "prompt_tokens_mean": (round(statistics.mean(
            r["prompt_tokens"] for r in ok)) if ok else None),
        "aggregate_tps": round(out_tokens / wall, 2),
    }
    if ok:
        gen = [r["gen_tps"] for r in ok if r["gen_tps"]]
        level.update({
            "per_request_gen_tps_mean": round(statistics.mean(gen), 2) if gen else None,
            "ttft_p50_s": round(percentile([r["ttft"] for r in ok], 50), 3),
            "ttft_p95_s": round(percentile([r["ttft"] for r in ok], 95), 3),
            "latency_p50_s": round(percentile([r["latency"] for r in ok], 50), 2),
            "latency_p95_s": round(percentile([r["latency"] for r in ok], 95), 2),
        })
    if joules is not None:
        level["cpu_package_joules"] = round(joules, 1)
        level["cpu_package_watts_mean"] = round(joules / wall, 1)
        if out_tokens:
            level["joules_per_output_token"] = round(joules / out_tokens, 3)
    return level


# kubectl port-forward's own output, for lost connections.
PF_LOG = open(os.devnull, "w")


def port_forward(local_port):
    """kubectl port-forward to the interceptor; returns the process."""
    proc = subprocess.Popen(
        ["kubectl", "-n", "keda", "port-forward",
         "svc/keda-add-ons-http-interceptor-proxy", f"{local_port}:8080"],
        stdout=PF_LOG, stderr=subprocess.STDOUT, text=True)
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", local_port), timeout=1).close()
            return proc
        except OSError:
            if proc.poll() is not None:
                sys.exit("port-forward exited (see its log)")
            time.sleep(0.2)
    proc.terminate()
    sys.exit("port-forward did not come up")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--levels", default="1,2,4,8")
    ap.add_argument("--requests", type=int, default=16,
                    help="requests per concurrency level (default 16)")
    ap.add_argument("--max-tokens", type=int, default=128)
    ap.add_argument("--timeout", type=float, default=600)
    ap.add_argument("--url", help="host:port of the interceptor; default: "
                    "start kubectl port-forward on 127.0.0.1:18080")
    ap.add_argument("--rapl", action="store_true",
                    help="measure CPU package energy (sudo -n cat RAPL)")
    ap.add_argument("--idle", type=int, default=0, metavar="SECONDS",
                    help="with --rapl: also measure idle power for this long "
                    "(server warm, no requests) before the load")
    ap.add_argument("--out", help="write the results as JSON to this file")
    args = ap.parse_args()

    pf = None
    global PF_LOG
    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        PF_LOG = open(os.path.splitext(args.out)[0] + ".port-forward.log", "w")
    if args.url:
        host, port = args.url.rsplit(":", 1)
        port = int(port)
    else:
        host, port = "127.0.0.1", 18080
        pf = port_forward(port)
    energy = Energy(args.rapl)
    if args.rapl:
        read_energy_uj()  # fail early without sudo

    try:
        print("warm-up (a cold start, if the server is at zero)...", flush=True)
        w = one_request(host, port, 0, 16, args.timeout)
        if "error" in w:
            sys.exit(f"warm-up failed: {w['error']}")
        print(f"  {w['latency']:.1f} s", flush=True)

        result = {
            "started": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "model": MODEL,
            "max_tokens": args.max_tokens,
            "requests_per_level": args.requests,
            "kubeconfig": os.path.basename(os.environ.get("KUBECONFIG", "")),
            "levels": [],
        }
        if args.rapl and args.idle:
            print(f"idle power, {args.idle} s...", flush=True)
            energy.start()
            time.sleep(args.idle)
            joules, secs = energy.stop()
            result["idle_cpu_package_watts"] = round(joules / secs, 1)
            print(f"  {result['idle_cpu_package_watts']} W", flush=True)

        next_id = 1000
        for c in (int(x) for x in args.levels.split(",")):
            print(f"concurrency {c}: {args.requests} requests...", flush=True)
            level = run_level(host, port, c, args.requests, args.max_tokens,
                              args.timeout, energy, next_id)
            next_id += args.requests
            result["levels"].append(level)
            print("  " + json.dumps(level), flush=True)
    finally:
        if pf:
            pf.terminate()

    print()
    print("| Concurrency | Requests | Errors | Output tok/s (all) | "
          "Gen tok/s per request | TTFT p50 | TTFT p95 | Latency p50 | "
          "Latency p95 | CPU W | J/token |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for lv in result["levels"]:
        print(f"| {lv['concurrency']} | {lv['requests']} | {lv['errors']} | "
              f"{lv['aggregate_tps']} | {lv.get('per_request_gen_tps_mean')} | "
              f"{lv.get('ttft_p50_s')} s | {lv.get('ttft_p95_s')} s | "
              f"{lv.get('latency_p50_s')} s | {lv.get('latency_p95_s')} s | "
              f"{lv.get('cpu_package_watts_mean', '-')} | "
              f"{lv.get('joules_per_output_token', '-')} |")
    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w") as f:
            json.dump(result, f, indent=2)
        print(f"\nwritten to {args.out}")


if __name__ == "__main__":
    main()

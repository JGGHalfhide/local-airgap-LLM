#!/usr/bin/env python3
"""Memory of the inference pod under an agent's request sequence.

Replays recorded chat requests (agent tool loops: multi-turn, 1k-7k-token
prompts; local-agents' ADR 0002 spike, 11/24) through the KEDA interceptor,
sequentially, and samples the pod's cgroup on the node every 2 s
(memory.current, memory.peak, anon, file, oom_kill) over ssh: no exec into
the pod, no change to the cluster. Found because the spike OOM-killed
llama-server at its 6 GiB limit with --cache-ram 0 (local-agents NOTES.txt).

  KUBECONFIG=... scripts/agent-mem-replay.py <requests.json> <out-dir> --label E1 [--slot N]

--slot N adds "id_slot": N to every request (all on one slot); without it
the server picks slots as it did in the spike. Waits until llama-server is
at zero replicas first (a fresh process per experiment), then cold-starts
it with the first request. Writes <out>/<label>-samples.jsonl,
<out>/<label>-requests.jsonl and <out>/<label>-server.log (llama-server's
log after the run, from kubectl logs). Stdlib only.
"""
import argparse
import json
import pathlib
import subprocess
import threading
import time
import urllib.error
import urllib.request

NODE, KEY, USER = "192.168.122.10", str(pathlib.Path.home() / ".ssh/platform-lab_ecdsa"), "rocky"
PORT = 18083


def kubectl(*a):
    return subprocess.run(["kubectl", *a], capture_output=True, text=True).stdout


def replicas():
    return int(kubectl("-n", "llm", "get", "deploy", "llama-server", "-o", "jsonpath={.status.replicas}") or 0)


def pod_uid():
    return kubectl("-n", "llm", "get", "pod", "-l", "app.kubernetes.io/name=llama-server",
                   "-o", "jsonpath={.items[0].metadata.uid}").strip()


REMOTE = r"""
d=$(ls -d /sys/fs/cgroup/kubepods.slice/kubepods-*.slice/kubepods-*-pod{uid}.slice 2>/dev/null | head -1)
[ -z "$d" ] && d=$(ls -d /sys/fs/cgroup/kubepods.slice/kubepods-pod{uid}.slice 2>/dev/null | head -1)
while true; do
  [ -d "$d" ] || {{ echo "GONE"; exit 0; }}
  echo "$(date +%s.%N) $(cat $d/memory.current) $(cat $d/memory.peak 2>/dev/null || echo -1) \
$(awk '$1=="anon"||$1=="file"{{printf "%s ", $2}}' $d/memory.stat) $(awk '$1=="oom_kill"{{print $2}}' $d/memory.events)"
  sleep 2
done
"""


def sampler(uid, path, stop):
    p = subprocess.Popen(["ssh", "-i", KEY, "-o", "BatchMode=yes", "-o", "LogLevel=ERROR", f"{USER}@{NODE}",
                          "bash", "-s"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    p.stdin.write(REMOTE.format(uid=uid.replace("-", "_")))
    p.stdin.close()
    with open(path, "a") as f:
        for line in p.stdout:
            parts = line.split()
            if parts and parts[0] == "GONE":
                f.write(json.dumps({"gone": True, "t": time.time()}) + "\n")
                break
            if len(parts) >= 6:
                t, cur, peak, anon, file_, oom = parts[:6]
                f.write(json.dumps({"t": float(t), "current_mib": int(cur) >> 20, "peak_mib": int(peak) >> 20 if int(peak) > 0 else None,
                                    "anon_mib": int(anon) >> 20, "file_mib": int(file_) >> 20, "oom_kill": int(oom)}) + "\n")
                f.flush()
            if stop.is_set():
                break
    p.kill()


def post(body, timeout=900):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Host": "llm-api.llm.svc", "Content-Type": "application/json"})
    t = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            j = json.load(r)
            return r.status, j, time.time() - t
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read()[:500].decode(errors="replace")}, time.time() - t
    except Exception as e:
        return 0, {"error": repr(e)}, time.time() - t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("requests")
    ap.add_argument("out")
    ap.add_argument("--label", required=True)
    ap.add_argument("--slot", type=int)
    ap.add_argument("--zero-timeout", type=int, default=900)
    a = ap.parse_args()
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    reqs = json.loads(pathlib.Path(a.requests).read_text())
    print(f"{a.label}: waiting for llama-server at 0 replicas (fresh process)...", flush=True)
    t0 = time.time()
    while replicas() != 0:
        if time.time() - t0 > a.zero_timeout:
            raise SystemExit("llama-server did not scale to zero")
        time.sleep(15)
    pf = subprocess.Popen(["kubectl", "-n", "keda", "port-forward", "svc/keda-add-ons-http-interceptor-proxy",
                           f"{PORT}:8080"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(3)
    stop = threading.Event()
    th = None
    try:
        for i, q in enumerate(reqs):
            body = dict(q["body"])
            if a.slot is not None:
                body["id_slot"] = a.slot
            if th is None:
                # the first request triggers the cold start; sample as soon as the pod has a uid
                res = {}
                worker = threading.Thread(target=lambda: res.update(r=post(body)))
                worker.start()
                for _ in range(120):
                    uid = pod_uid()
                    if uid:
                        break
                    time.sleep(1)
                th = threading.Thread(target=sampler, args=(uid, out / f"{a.label}-samples.jsonl", stop), daemon=True)
                th.start()
                worker.join()
                status, j, dt = res["r"]
            else:
                status, j, dt = post(body)
            u = (j or {}).get("usage") or {}
            rec = {"i": i, "run": q["run"], "t_end": time.time(), "status": status, "seconds": round(dt, 2),
                   "prompt_tokens": u.get("prompt_tokens"), "completion_tokens": u.get("completion_tokens"),
                   "cached_tokens": (u.get("prompt_tokens_details") or {}).get("cached_tokens"),
                   "error": (j or {}).get("error")}
            with open(out / f"{a.label}-requests.jsonl", "a") as f:
                f.write(json.dumps(rec) + "\n")
            print(f"{a.label} {i:2d} {q['run']:<28} {status} {dt:6.1f}s prompt={rec['prompt_tokens']}", flush=True)
        time.sleep(6)
    finally:
        stop.set()
        pf.terminate()
        (out / f"{a.label}-server.log").write_text(kubectl("-n", "llm", "logs", "deploy/llama-server") +
                                                   "\n--- previous ---\n" + kubectl("-n", "llm", "logs", "deploy/llama-server", "--previous"))
        (out / f"{a.label}-pod.json").write_text(kubectl("-n", "llm", "get", "pod", "-l", "app.kubernetes.io/name=llama-server", "-o", "json"))


if __name__ == "__main__":
    main()

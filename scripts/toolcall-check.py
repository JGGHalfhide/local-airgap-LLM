#!/usr/bin/env python3
"""Small agent-readiness check for each candidate model (not a quality benchmark).

Starts llama-server (--jinja, so each model's own chat template handles tools)
and sends five OpenAI-style requests a Phase 3 agent would make:
  single_tool    one tool offered; correct name and arguments
  pick_tool      three tools offered; picks the right one with right arguments
  no_tool        tools offered but the question needs none; answers directly
  json_schema    response_format json_schema; output parses and matches
  tool_result    after a tool result, gives a final answer using it
Temperature 0; thinking disabled where the template supports it (CPU latency).
Results go to benchmarks/results/<label>/toolcall-<name>.json.

Usage: scripts/toolcall-check.py --label host [name ...]
"""

import argparse
import json
import subprocess
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "build/llama.cpp/build/bin/llama-server"
PORT = 18080


def fn(name, desc, props, required):
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props, "required": required}}}


POD_STATUS = fn("get_pod_status", "Get the status of a Kubernetes pod.",
                {"namespace": {"type": "string"}, "pod": {"type": "string"}}, ["namespace", "pod"])
POD_LOGS = fn("get_pod_logs", "Get the last N log lines of a Kubernetes pod.",
              {"namespace": {"type": "string"}, "pod": {"type": "string"}, "lines": {"type": "integer"}},
              ["namespace", "pod", "lines"])
SEARCH = fn("search_runbooks", "Search the platform runbooks.", {"query": {"type": "string"}}, ["query"])
RESTART = fn("restart_deployment", "Restart a Kubernetes deployment.",
             {"namespace": {"type": "string"}, "deployment": {"type": "string"}}, ["namespace", "deployment"])

SYSTEM = {"role": "system", "content": "You are a platform operations assistant. Use tools when they help."}

TRIAGE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["severity", "component", "summary"],
                 "properties": {"severity": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
                                "component": {"type": "string"}, "summary": {"type": "string"}}}


def args_of(msg):
    calls = msg.get("tool_calls") or []
    if len(calls) != 1:
        return None, None
    f = calls[0]["function"]
    try:
        return f["name"], json.loads(f["arguments"])
    except (json.JSONDecodeError, TypeError):
        return f["name"], None


def check_single(msg):
    name, a = args_of(msg)
    return name == "get_pod_status" and a == {"namespace": "llm", "pod": "inference-0"}


def check_pick(msg):
    name, a = args_of(msg)
    return name == "get_pod_logs" and a is not None and a.get("namespace") == "web" \
        and a.get("pod") == "api-7f" and int(a.get("lines", -1)) == 50


def check_no_tool(msg):
    return not msg.get("tool_calls") and "kubernetes" in (msg.get("content") or "").lower()


def check_json(msg):
    try:
        d = json.loads(msg.get("content") or "")
    except json.JSONDecodeError:
        return False
    return set(d) == {"severity", "component", "summary"} and d["severity"] in ("high", "critical") \
        and isinstance(d["component"], str) and isinstance(d["summary"], str)


def check_result(msg):
    c = (msg.get("content") or "").lower()
    return not msg.get("tool_calls") and "crashloopbackoff" in c.replace(" ", "")


CASES = [
    ("single_tool", dict(tools=[POD_STATUS], messages=[SYSTEM, {"role": "user", "content":
        "Is the pod inference-0 in namespace llm healthy?"}]), check_single),
    ("pick_tool", dict(tools=[SEARCH, POD_LOGS, RESTART], messages=[SYSTEM, {"role": "user", "content":
        "Show me the last 50 log lines of pod api-7f in namespace web."}]), check_pick),
    ("no_tool", dict(tools=[SEARCH, POD_LOGS, RESTART], messages=[SYSTEM, {"role": "user", "content":
        "In one sentence: what is RKE2? Mention Kubernetes."}]), check_no_tool),
    ("json_schema", dict(response_format={"type": "json_schema", "json_schema": {"name": "triage", "schema": TRIAGE_SCHEMA}},
        messages=[SYSTEM, {"role": "user", "content":
            "Triage this alert as JSON. Alert: etcd on the only control-plane node has had no leader for "
            "10 minutes; the API server returns errors to every request."}]), check_json),
    ("tool_result", dict(tools=[POD_STATUS], messages=[SYSTEM,
        {"role": "user", "content": "Is the pod inference-0 in namespace llm healthy?"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "call_1", "type": "function", "function": {
            "name": "get_pod_status", "arguments": json.dumps({"namespace": "llm", "pod": "inference-0"})}}]},
        {"role": "tool", "tool_call_id": "call_1", "content": json.dumps(
            {"phase": "Running", "ready": False, "restarts": 14, "reason": "CrashLoopBackOff"})}]), check_result),
]


def post(path, body, timeout=600):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def wait_ready(proc, timeout=180):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if proc.poll() is not None:
            raise RuntimeError("llama-server exited")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=2) as r:
                if r.status == 200:
                    return time.monotonic() - t0
        except OSError:
            pass
        time.sleep(0.5)
    raise RuntimeError("llama-server not ready")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("names", nargs="*")
    a = ap.parse_args()
    outdir = ROOT / "benchmarks/results" / a.label
    outdir.mkdir(parents=True, exist_ok=True)

    for line in (ROOT / "benchmarks/candidates.tsv").read_text().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        name, _, _, file, _, _ = line.split("\t")
        if a.names and name not in a.names:
            continue
        print(f"== {name}", flush=True)
        (ROOT / "build").mkdir(exist_ok=True)
        log = open(ROOT / "build" / f"toolcall-{a.label}-{name}.server.log", "w")
        proc = subprocess.Popen([str(SERVER), "-m", str(ROOT / "models" / file), "--jinja", "-t", "8",
                                 "-c", "8192", "--host", "127.0.0.1", "--port", str(PORT)],
                                stdout=log, stderr=subprocess.STDOUT)
        try:
            ready_s = wait_ready(proc)
            results = []
            for cname, body, check in CASES:
                body = dict(body, temperature=0, max_tokens=512,
                            chat_template_kwargs={"enable_thinking": False})
                t0 = time.monotonic()
                try:
                    msg = post("/v1/chat/completions", body)["choices"][0]["message"]
                    err = None
                except Exception as e:  # record server-side template/parse errors as failures
                    msg, err = {}, str(e)
                secs = round(time.monotonic() - t0, 2)
                ok = bool(msg) and check(msg)
                results.append(dict(case=cname, ok=ok, seconds=secs, error=err,
                                    content=msg.get("content"), tool_calls=msg.get("tool_calls")))
                print(f"   {cname:12} {'PASS' if ok else 'FAIL'} {secs:6.1f}s", flush=True)
        finally:
            proc.terminate()
            proc.wait()
            log.close()
        score = sum(r["ok"] for r in results)
        (outdir / f"toolcall-{name}.json").write_text(json.dumps(
            dict(model=name, server_ready_s=round(ready_s, 2), passed=score, total=len(results),
                 results=results), indent=2) + "\n")
        print(f"   {score}/{len(results)} passed", flush=True)


if __name__ == "__main__":
    main()

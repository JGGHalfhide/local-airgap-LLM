#!/usr/bin/env python3
"""Qwen3.5 thinking: cost, effect on answers, and server controls (host).

Starts the host llama-server build (same commit as the runtime image) with
the deployed flags (deploy/llm/llama-server.yaml) and runs:

  1. cost and accuracy: ten prompts with checkable answers (easy facts,
     multi-step arithmetic and logic, incident triage), each with thinking
     on and off per request (chat_template_kwargs). Records completion
     tokens, thinking length, latency and whether the answer is right.
  2. server-wide off: with `--reasoning off` and with
     `--chat-template-kwargs {"enable_thinking":false}`, does a request that
     asks for thinking (enable_thinking true) still get it?
  3. budget: with `--reasoning off --reasoning-budget N` (the deployed
     setting since v1.1), requests that opt in to thinking: are the
     thinking tokens capped, and is there still a (right) answer after the
     cut? A request without the opt-in must not think.

Temperature 0. Results: build/thinking-check[-<sections>].json; summary on stdout.
Usage: scripts/thinking-check.py [--model models/<file>.gguf] [--budget 1024]
                                 [--sections 1,2,3]
"""
import argparse
import json
import subprocess
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "build/llama.cpp/build/bin/llama-server"
PORT = 18083
# deploy/llm/llama-server.yaml, minus the network and metrics flags.
DEPLOY_FLAGS = ["--jinja", "--load-mode", "none", "--cache-ram", "0",
                "--ctx-size", "32768", "--threads", "6", "--threads-batch", "8"]


def has(*words):
    return lambda a: all(w in a.lower() for w in words)


def number(n):
    def check(a):
        tokens = a.replace(",", " ").replace("GiB", " ").split()
        return any(t.strip(".s") == n for t in tokens)
    return check


SHORT = " Answer with just the number."
PROMPTS = [
    ("fact", "What is the capital of France? One word.", has("paris")),
    ("count_letters", "How many times does the letter r appear in the word strawberry?" + SHORT,
     number("3")),
    ("memory_left", "A node has 16 GiB of memory. The platform uses 6.5 GiB and the inference "
     "pod has a 6 GiB limit. How many GiB are left for other pods?" + SHORT, number("3.5")),
    ("request_time", "A request spends 2 seconds on prompt processing, then generates 128 tokens "
     "at 14 tokens per second. How many seconds does it take in total, rounded to one decimal?"
     + SHORT, number("11.1")),
    ("startup_chain", "Pod A starts only after pod B is ready, and B only after C is ready. C "
     "takes 5 s to become ready, B 3 s and A 2 s. How many seconds until A is ready?" + SHORT,
     number("10")),
    ("replicas", "A deployment has 3 replicas. I scale it down by 1, then double it, then "
     "remove 2. How many replicas are left?" + SHORT, number("2")),
    ("slots_queue", "A server has 4 slots and handles one request per slot at a time. Every "
     "request takes 20 seconds. 9 requests arrive at the same moment. After how many seconds "
     "does the last one finish?" + SHORT, number("60")),
    ("triage_oom", "A pod restarted 4 times. kubectl shows lastState terminated, reason "
     "OOMKilled, exit code 137; its memory limit is 6Gi. In one sentence, what is the most "
     "likely cause?", has("memory")),
    ("triage_pull", "A pod is in ImagePullBackOff. The event says: failed to pull image "
     "127.0.0.1:30500/library/postgres@sha256:a77a...: NotFound. The registry is up and other "
     "images pull fine. In one sentence, what is the most likely cause?",
     lambda a: any(w in a.lower() for w in ("digest", "not been pushed", "not pushed",
                                            "not published", "missing", "does not exist",
                                            "doesn't exist", "not present", "not in the registry"))),
    ("triage_policy", "Kyverno denied a pod: 'image is not signed with the platform-lab cosign "
     "key'. The image is in the registry. In one sentence, what should the operator do?",
     lambda a: "sign" in a.lower()),
]


def post(body, timeout=900):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/v1/chat/completions",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        resp = json.load(r)
    msg = resp["choices"][0]["message"]
    return {
        "seconds": round(time.monotonic() - t0, 2),
        "completion_tokens": resp["usage"]["completion_tokens"],
        "reasoning_chars": len(msg.get("reasoning_content") or ""),
        "answer": (msg.get("content") or "").strip(),
        "finish_reason": resp["choices"][0].get("finish_reason"),
    }


def ask(prompt, thinking, max_tokens):
    body = {"model": "qwen", "temperature": 0, "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}]}
    if thinking is not None:
        body["chat_template_kwargs"] = {"enable_thinking": thinking}
    return post(body)


class Server:
    def __init__(self, model, extra, label):
        self.log = open(ROOT / "build" / f"thinking-check-{label}.server.log", "w")
        self.proc = subprocess.Popen(
            [str(SERVER), "-m", str(model), *DEPLOY_FLAGS, *extra,
             "--host", "127.0.0.1", "--port", str(PORT)],
            stdout=self.log, stderr=subprocess.STDOUT)
        for _ in range(360):
            if self.proc.poll() is not None:
                raise RuntimeError(f"llama-server exited ({label}); see its log")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=2) as r:
                    if r.status == 200:
                        return
            except OSError:
                pass
            time.sleep(0.5)
        raise RuntimeError("llama-server not ready")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.proc.terminate()
        self.proc.wait()
        self.log.close()


class NullServer:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=str(ROOT / "models/Qwen3.5-4B-Q4_K_M.gguf"))
    ap.add_argument("--budget", type=int, default=1024)
    ap.add_argument("--sections", default="1,2,3")
    a = ap.parse_args()
    sections = set(a.sections.split(","))
    (ROOT / "build").mkdir(exist_ok=True)
    out = {"model": Path(a.model).name, "flags": DEPLOY_FLAGS}

    rows = []
    if "1" in sections:
        print("== 1. thinking on vs off, per request (server defaults)", flush=True)
    with (Server(a.model, [], "default") if "1" in sections else NullServer()):
        for name, prompt, check in (PROMPTS if "1" in sections else []):
            row = {"case": name}
            for mode, think, max_tokens in (("off", False, 256), ("on", True, 8192)):
                r = ask(prompt, think, max_tokens)
                r["correct"] = check(r["answer"])
                row[mode] = r
            # And with no chat_template_kwargs at all: what a client gets by default.
            if name == "fact":
                row["default"] = ask(prompt, None, 8192)
            rows.append(row)
            print(f"   {name:14} off: {row['off']['completion_tokens']:5} tok "
                  f"{row['off']['seconds']:6.1f}s {'ok ' if row['off']['correct'] else 'BAD'} | "
                  f"on: {row['on']['completion_tokens']:5} tok {row['on']['seconds']:6.1f}s "
                  f"{'ok ' if row['on']['correct'] else 'BAD'}", flush=True)
    out["per_request"] = rows

    if "2" in sections:
        print("== 2. server-wide off; does a request still get thinking if it asks?", flush=True)
    out["server_off"] = {}
    for label, extra in ((("reasoning-off", ["--reasoning", "off"]),
                          ("template-kwargs-off",
                           ["--chat-template-kwargs", '{"enable_thinking":false}']))
                         if "2" in sections else ()):
        with Server(a.model, extra, label):
            res = {"default": ask(PROMPTS[6][1], None, 8192),
                   "request_on": ask(PROMPTS[6][1], True, 8192)}
        out["server_off"][label] = res
        print(f"   {label:20} no kwargs: {res['default']['reasoning_chars']} thinking chars | "
              f"request enable_thinking=true: {res['request_on']['reasoning_chars']} thinking "
              f"chars", flush=True)

    budget = []
    if "3" in sections:
        flags = ["--reasoning", "off", "--reasoning-budget", str(a.budget)]
        print(f"== 3. {' '.join(flags)}; requests opt in", flush=True)
        with Server(a.model, flags, f"budget-{a.budget}"):
            r = ask(PROMPTS[0][1], None, 8192)
            out["budget_no_opt_in"] = r
            print(f"   no opt-in: {r['reasoning_chars']} thinking chars, "
                  f"{r['completion_tokens']} tok, {r['answer'][:40]!r}", flush=True)
            for name, prompt, check in PROMPTS:
                r = ask(prompt, True, 8192)
                r["correct"] = check(r["answer"])
                r["case"] = name
                budget.append(r)
                print(f"   {name:14} {r['completion_tokens']:5} tok {r['seconds']:6.1f}s "
                      f"thinking {r['reasoning_chars']} chars, answer "
                      f"{'ok ' if r['correct'] else 'BAD'} {r['answer'][:60]!r}", flush=True)
    out[f"budget_{a.budget}"] = budget

    suffix = "" if sections == {"1", "2", "3"} else "-" + "".join(sorted(sections))
    path = ROOT / "build" / f"thinking-check{suffix}.json"
    path.write_text(json.dumps(out, indent=2) + "\n")
    print(f"written to {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

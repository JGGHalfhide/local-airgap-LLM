#!/usr/bin/env python3
"""Benchmark candidate GGUF models with llama-bench (CPU only).

For each model in benchmarks/candidates.tsv that is present in models/:
  pass 1: prompt processing (pp512) and generation (tg128) at an empty
          context, swept over thread counts
  pass 2: the same at 4096 tokens of existing context (-d 4096), at one
          thread count: closer to a RAG or agent request
  load:   wall time of a minimal run (load + 1 token, warm page cache)
Peak RSS of each llama-bench process is recorded (getrusage on the child).

Raw llama-bench JSON and a summary go to benchmarks/results/<label>/.
Usage: scripts/bench.py --label host [--threads 4,8,16] [--depth-threads 8]
       [--reps 3] [name ...]
"""

import argparse
import json
import os
import platform
import resource
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BENCH = ROOT / "build/llama.cpp/build/bin/llama-bench"


def candidates():
    for line in (ROOT / "benchmarks/candidates.tsv").read_text().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        name, repo, rev, file, sha, lic = line.split("\t")
        yield dict(name=name, repo=repo, revision=rev, file=file, sha256=sha, license=lic)


def run(args):
    """Run llama-bench; return (parsed JSON, wall seconds)."""
    t0 = time.monotonic()
    p = subprocess.run([str(BENCH), "-o", "json", *args], capture_output=True, text=True)
    wall = time.monotonic() - t0
    if p.returncode != 0:
        sys.exit(f"llama-bench failed: {' '.join(args)}\n{p.stderr[-2000:]}")
    return json.loads(p.stdout), wall


def rss_alone(model, threads):
    """Peak RSS of a single fresh process (forked, so the child max is its own)."""
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(r)
        p = subprocess.run([str(BENCH), "-m", model, "-t", str(threads), "-p", "512", "-n", "16", "-r", "1"],
                           capture_output=True)
        mib = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024
        os.write(w, f"{p.returncode} {mib}".encode())
        os._exit(0)
    os.close(w)
    rc, mib = os.read(r, 64).decode().split()
    os.waitpid(pid, 0)
    return float(mib) if rc == "0" else None


def summarise(rows):
    out = []
    for r in rows:
        kind = "pp" if r["n_prompt"] else "tg"
        out.append(dict(test=f"{kind}{r['n_prompt'] or r['n_gen']}", depth=r.get("n_depth", 0),
                        threads=r["n_threads"], tps=round(r["avg_ts"], 2), tps_sd=round(r["stddev_ts"], 2)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True, help="where it ran, e.g. host or build-vm")
    ap.add_argument("--threads", default="4,8,16")
    ap.add_argument("--depth-threads", default="8")
    ap.add_argument("--depth", default="4096")
    ap.add_argument("--reps", default="3")
    ap.add_argument("names", nargs="*")
    a = ap.parse_args()

    outdir = ROOT / "benchmarks/results" / a.label
    outdir.mkdir(parents=True, exist_ok=True)
    commit = subprocess.run(["git", "-C", str(ROOT / "build/llama.cpp"), "rev-parse", "HEAD"],
                            capture_output=True, text=True).stdout.strip()
    cpu = next((l.split(":", 1)[1].strip() for l in Path("/proc/cpuinfo").read_text().splitlines()
                if l.startswith("model name")), platform.processor())
    meminfo = Path("/proc/meminfo").read_text().split("\n")[0]

    for m in candidates():
        if a.names and m["name"] not in a.names:
            continue
        path = ROOT / "models" / m["file"]
        if not path.exists():
            print(f"skip {m['name']}: {path.name} not downloaded")
            continue
        print(f"== {m['name']}", flush=True)
        common = ["-m", str(path), "-r", a.reps]

        _, load_s = run(["-m", str(path), "-p", "0", "-n", "1", "-r", "1", "-t", "8"])
        sweep, _ = run([*common, "-t", a.threads, "-p", "512", "-n", "128"])
        deep, _ = run([*common, "-t", a.depth_threads, "-p", "512", "-n", "128", "-d", a.depth])
        rss = rss_alone(str(path), 8)

        result = dict(model=m, label=a.label, cpu=cpu, nproc=os.cpu_count(), mem=meminfo,
                      llama_cpp_commit=commit, load_plus_1tok_s=round(load_s, 2),
                      peak_rss_mib=round(rss) if rss else None,
                      file_gib=round(path.stat().st_size / 2**30, 2),
                      results=summarise(sweep + deep), raw=sweep + deep)
        (outdir / f"{m['name']}.json").write_text(json.dumps(result, indent=2) + "\n")
        for r in result["results"]:
            print(f"   {r['test']:>6} d{r['depth']:<5} t{r['threads']:<3} {r['tps']:8.2f} ± {r['tps_sd']} tok/s")
        print(f"   load+1tok {result['load_plus_1tok_s']} s, peak RSS {result['peak_rss_mib']} MiB", flush=True)


if __name__ == "__main__":
    main()

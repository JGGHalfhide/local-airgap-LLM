#!/usr/bin/env python3
"""Generate the llama-server Grafana dashboard (dashboard as code).

Writes deploy/monitoring/dashboards/llama-server.json, which kustomize turns
into a ConfigMap that Grafana's sidecar loads. Edit this script, not the
JSON, then run `make dashboard`. Standard library only.

Data sources:
- llama-server's own /metrics (llamacpp:*): token counters and timers,
  requests processing/deferred, busy slots. llama-server exports no latency
  histograms, so time to first token and p50/p95 latency come from the
  client side (the Week 6 load test), not from this dashboard.
- cAdvisor and kube-state-metrics (platform-lab's kube-prometheus-stack):
  memory, CPU, replicas, restarts.
"""
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "deploy/monitoring/dashboards/llama-server.json"

DS = {"type": "prometheus", "uid": "prometheus"}
SEL = 'namespace="llm"'
CSEL = 'namespace="llm", container="llama-server"'
RATE = "$__rate_interval"

panels = []
_next_id = 1


def panel(kind, title, targets, x, y, w, h, unit="short", description="", **extra):
    global _next_id
    p = {
        "id": _next_id,
        "type": kind,
        "title": title,
        "description": description,
        "datasource": DS,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "targets": [
            {"refId": chr(65 + i), "expr": expr, "legendFormat": legend, "datasource": DS}
            for i, (expr, legend) in enumerate(targets)
        ],
        "fieldConfig": {"defaults": {"unit": unit}, "overrides": []},
    }
    _next_id += 1
    p.update(extra)
    panels.append(p)
    return p


def stat(title, expr, x, y, unit="short", description="", thresholds=None, mappings=None, decimals=None):
    p = panel("stat", title, [(expr, "")], x, y, 4, 4, unit, description,
              options={"reduceOptions": {"calcs": ["lastNotNull"]}, "colorMode": "value", "graphMode": "area"})
    p["targets"][0]["instant"] = False
    d = p["fieldConfig"]["defaults"]
    d["thresholds"] = {"mode": "absolute", "steps": thresholds or [{"color": "blue", "value": None}]}
    if mappings:
        d["mappings"] = mappings
    if decimals is not None:
        d["decimals"] = decimals


def series(title, targets, x, y, w=12, h=8, unit="short", description="", stack=False):
    p = panel("timeseries", title, targets, x, y, w, h, unit, description,
              options={"legend": {"displayMode": "list", "placement": "bottom"},
                       "tooltip": {"mode": "multi"}})
    p["fieldConfig"]["defaults"]["custom"] = {
        "lineWidth": 2, "fillOpacity": 10, "showPoints": "never",
        "stacking": {"mode": "normal" if stack else "none"},
    }


# Row 1: status at a glance.
stat("Ready replicas", f'sum(kube_deployment_status_replicas_available{{{SEL}, deployment="llama-server"}}) or vector(0)',
     0, 0, description="0 when KEDA has scaled the server to zero.",
     thresholds=[{"color": "text", "value": None}, {"color": "green", "value": 1}])
stat("Requests processing", f'sum(llamacpp:requests_processing{{{SEL}}}) or vector(0)', 4, 0,
     description="Requests occupying a slot right now (4 slots).")
stat("Requests deferred", f'sum(llamacpp:requests_deferred{{{SEL}}}) or vector(0)', 8, 0,
     description="Requests waiting for a free slot.",
     thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 1}])
stat("Generation speed", f'sum(rate(llamacpp:tokens_predicted_total{{{SEL}}}[5m])) / sum(rate(llamacpp:tokens_predicted_seconds_total{{{SEL}}}[5m]))',
     12, 0, unit="none", decimals=1,
     description="Generated tokens per second of generation time, last 5 min (tok/s per request).")
stat("Prompt speed", f'sum(rate(llamacpp:prompt_tokens_total{{{SEL}}}[5m])) / sum(rate(llamacpp:prompt_seconds_total{{{SEL}}}[5m]))',
     16, 0, unit="none", decimals=0,
     description="Prompt tokens per second of prompt-processing time, last 5 min.")
stat("Memory (working set)", f'sum(container_memory_working_set_bytes{{{CSEL}}})', 20, 0, unit="bytes",
     description="Includes reclaimable page cache from reading the model; the process itself peaks at about 4.7 GiB at 32k context.",
     thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 5.5 * 2**30}])

# Row 2: throughput.
series("Throughput while busy (tok/s)", [
    (f'sum(rate(llamacpp:tokens_predicted_total{{{SEL}}}[{RATE}])) / sum(rate(llamacpp:tokens_predicted_seconds_total{{{SEL}}}[{RATE}]))', "generation"),
    (f'sum(rate(llamacpp:prompt_tokens_total{{{SEL}}}[{RATE}])) / sum(rate(llamacpp:prompt_seconds_total{{{SEL}}}[{RATE}]))', "prompt"),
], 0, 4, unit="none",
    description="Tokens per second of time spent on them (the speed a request sees). Gaps mean idle.")
series("Tokens served (tok/s)", [
    (f'sum(rate(llamacpp:tokens_predicted_total{{{SEL}}}[{RATE}]))', "generated"),
    (f'sum(rate(llamacpp:prompt_tokens_total{{{SEL}}}[{RATE}]))', "prompt (not cached)"),
    (f'sum(rate(llamacpp:prompt_tokens_cached_total{{{SEL}}}[{RATE}]))', "prompt (from cache)"),
], 12, 4, unit="none", description="Tokens per wall-clock second, all requests together.")

# Row 3: load.
series("Requests", [
    (f'sum(llamacpp:requests_processing{{{SEL}}})', "processing"),
    (f'sum(llamacpp:requests_deferred{{{SEL}}})', "deferred"),
], 0, 12, description="The signal for scaling: in flight and queued.")
series("Slots and sequence length", [
    (f'avg(llamacpp:n_busy_slots_per_decode{{{SEL}}})', "busy slots per decode (avg)"),
], 12, 12, h=8, description="Average number of slots batched per decode call (1-4).")

# Row 4: resources.
series("Memory", [
    (f'sum(container_memory_working_set_bytes{{{CSEL}}})', "working set"),
    (f'sum(container_memory_rss{{{CSEL}}})', "RSS (process)"),
    (f'sum(kube_pod_container_resource_limits{{{CSEL}, resource="memory"}})', "limit"),
    (f'sum(kube_pod_container_resource_requests{{{CSEL}, resource="memory"}})', "request"),
], 0, 20, unit="bytes")
series("CPU (cores)", [
    (f'sum(rate(container_cpu_usage_seconds_total{{{CSEL}}}[{RATE}]))', "used"),
    (f'sum(kube_pod_container_resource_requests{{{CSEL}, resource="cpu"}})', "request"),
], 12, 20, unit="none", description="6 threads generating, 8 processing prompts; no CPU limit.")

# Row 5: lifecycle (scaling, cold start, restarts).
series("Replicas", [
    (f'sum(kube_deployment_spec_replicas{{{SEL}, deployment="llama-server"}})', "desired"),
    (f'sum(kube_deployment_status_replicas_available{{{SEL}, deployment="llama-server"}})', "ready"),
], 0, 28, description="Desired vs ready: the gap is the cold start.")
series("Restarts and OOM kills", [
    (f'sum(increase(kube_pod_container_status_restarts_total{{{CSEL}}}[1h]))', "restarts (1h)"),
    (f'sum(kube_pod_container_status_last_terminated_reason{{{CSEL}, reason="OOMKilled"}})', "last terminated: OOMKilled"),
], 12, 28)

dashboard = {
    "uid": "llama-server",
    "title": "llama-server",
    "description": "Inference endpoint (local-airgap-LLM): throughput, load, resources, scaling. Generated by scripts/gen-dashboard.py.",
    "tags": ["local-airgap-llm", "inference"],
    "timezone": "browser",
    "schemaVersion": 39,
    "version": 1,
    "editable": False,
    "refresh": "30s",
    "time": {"from": "now-1h", "to": "now"},
    "templating": {"list": []},
    "annotations": {"list": []},
    "panels": panels,
}

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(dashboard, indent=2) + "\n")
print(f"wrote {OUT.relative_to(ROOT)} ({len(panels)} panels)")

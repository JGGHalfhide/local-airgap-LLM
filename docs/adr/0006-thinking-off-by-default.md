# 0006. Thinking off by default, opt-in with a 1024-token budget

Status: accepted (v1.1, 11/23)

## Context

Qwen3.5's chat template turns thinking on unless a request sends
`chat_template_kwargs: {"enable_thinking": false}`. The model then writes
its reasoning (returned as `reasoning_content`) before the answer, at the
same ~14 tokens/s as the answer itself. Every test script in this repo
turned it off per request, so the published numbers (load test, cold start,
tool calls) are thinking-off numbers; the server's own default was on, and
the smoke test (which sent no flag) spent 134 tokens on "Paris".

Measured on the host 11/23 (`scripts/thinking-check.py`, the deployed
server flags, temperature 0, ten prompts with checkable answers; results
in `benchmarks/results/thinking/`):

| Setting | Answered | Right | Time per answer |
|---|---|---|---|
| Thinking off | 10/10 | 6/10 | 0.3-5 s |
| Thinking on, no limit | 7/10 | 7/10 | 10-45 s; 3 open-ended triage questions looped to 8,192 tokens (10 min) with no answer |
| Thinking on, budget 256 | 10/10 | 6/10 | 10-20 s; the cut-off reasoning broke one answer thinking off had right |
| Thinking on, budget 1024 | 10/10 | 9/10 | 10-68 s |

- Thinking fixed all three multi-step questions (letter count, a time
  calculation, slot queueing) that the model got wrong without it.
- On open-ended prose the model reached its answer early and then kept
  rewording it ("final decision… wait…"): a loop that held a slot for
  10 minutes. Greedy decoding likely makes it worse; Qwen recommends
  sampling in thinking mode.
- Tool calling and JSON output (`scripts/toolcall-check.py`): 5/5 either
  way; thinking added 80-120 tokens per call, and 1,710 tokens (111 s) on
  the open-ended JSON triage case.
- `--reasoning off` and `--chat-template-kwargs '{"enable_thinking":false}'`
  both turn it off by default, and both still let a request opt in.
  With `--reasoning off --reasoning-budget 1024`, a request without the
  opt-in does not think and an opted-in one stops at the budget.

On this CPU a thinking request holds one of four slots for up to a minute
(more under load), so an unrequested default is paid by every client.

## Decision

- llama-server runs with `--reasoning off --reasoning-budget 1024`
  (`deploy/llm/llama-server.yaml`). Requests think only if they send
  `chat_template_kwargs: {"enable_thinking": true}`, and then for at most
  1024 tokens (about 70 s alone, longer under load). 1024 is above the
  245-685 tokens the multi-step prompts needed and stops a loop after about
  a minute.
- `make smoke` checks that a request without the flag gets no
  `reasoning_content`.
- When an agent should opt in, the sampling settings for thinking mode,
  whether reasoning is kept across turns (`--reasoning-preserve`), and
  whether thinking improves real triage answers are Phase 3 questions,
  to be settled with its evaluation set.

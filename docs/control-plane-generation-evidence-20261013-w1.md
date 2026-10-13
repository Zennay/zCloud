# zCloud worker generation evidence — operator acceptance (13 October 2026)

Owner: control-plane lane of [PR #1340](https://github.com/Zennay/zCloud/pull/1340). This document is a **non-mutating client workflow**, not authority to repair, restart, merge or deploy zCloud services.

## What is measured

The tool `scripts/zcloud_worker_generation_evidence_20261013_w1.py` reads the current queue allocation and worker-browser diagnostic via two `/api/runner-targets` GETs bracketing one `/api/worker-debug` GET. It accepts self-reported generation **only** when all of these are true:

1. The worker key, local/global slot, provider and queue identifier match the selected allocation and the current runtime target.
2. The target has `active=true`, `desired_state=running`, `assignment_ready=true` and a matching `queue_item.queue_id`.
3. The decoded claim time `queue_item.claimed_at` is valid UTC-offset-aware, and the event's optimistic reconstructed time is **not earlier** than this claim. The backend rounds age to whole seconds: this check rejects definitely earlier events, but cannot prove a close causal edge.
4. The debug row reports the same worker/queue/provider, `desired_state=running`, `verdict=generating` and a recent nonterminal `heartbeat`, `generation-started` or `generation-progress`.
5. No selected assignment, paused/running target status, provider or queue-claim epoch changes between the two local snapshots.

No evidence is inferred from `available`, `prompt-sent`, `force` queued, `started`, or a successful automation/CI run. Single-time browser telemetry does **not** prove a real visible tab, fresh generated assistant text, valid task completion, uninterrupted generation, or future worker progress.

## Safe execution after approval and trusted merge

From the checked-in project root on an authorized local instance:

```sh
python3 scripts/zcloud_worker_generation_evidence_20261013_w1.py
```

Exit status: `0` = all allocated ChatGPT workers reported fresh, sufficiently consistent generation; `1` = valid observations but one or more ChatGPT workers not generating; `2` = missing, stale, ambiguous or contradictory evidence. The output is a bounded status/count JSON, not a browser session dump. Retain source SHA, timestamp, sanitized exit class and CI URL when escalating.

For a **synthetic offline** fixture, both inputs are mandatory:
```sh
python3 scripts/zcloud_worker_generation_evidence_20261013_w1.py \
  --targets-file /safe/local/targets.json \
  --debug-file /safe/local/worker-debug.json
```
These files are not production traces; their content should not be forwarded to third parties or copied into PR logs.

A pre-approved zSSH recipe `ops/zssh-runner-recipes/zcloud-generation-check.py` is included for **post-merge trusted main only**. Do not attempt to invoke it from the unmerged PR and do not treat zSSH bridge SUCCESS as equivalent to a generating result. The recipe hides stdout/stderr and returns only a bounded exit code.

## Trust and side-effect boundaries

- Requests use fixed loopback URL, GET method, no proxy inheritance, no redirects, bounded JSON responses, and strict decoded Unicode/JSON shape checks.
- This CLI sends **no write methods** and does not touch services, filesystem state, workers, tabs or SQLite itself. However, the current zCloud server calls `runner_worker_targets(reconcile=True)` while servicing relevant GETs, which can perform **implicit SQLite reconciliation writes**. Consequently, `http_get_only` intentionally does **not** guarantee zero backend side effects. To guarantee truly immutable observations, the serialized backend owner must provide or approve a read-only endpoint/read-model.
- Allocation+debug cannot be transactionally read across HTTP endpoints. Bracketed reads catch ordinary assignment churn but not an ABA change; backend browser events currently do not carry a durable queue-claim ID. The timestamp check is only a lower-assurance guard against events definitely older than a new claim.
- A browser-reported generating heartbeat does not establish that the foreground assistant is writing new material content. Only a separate authorized DOM/receipt integration and temporal validation can establish that.
- The output deliberately omits prompts, conversation URLs, freeform error reasons, raw backend payloads and credentials. Evidence classification never grants production authority.

## Parallel handoff

[PR #1339](https://github.com/Zennay/zCloud/pull/1339) separately owns a read-only SQLite/runner-live reconciler. Do not mix source files, reassign its workers or silently merge conflicting evidence semantics. Actual tab lifecycle, browser/Firefox worker dispatch, `server.py`, `project_runtime.py` and serialized #580/PWQ-41 + #1089 releases remain with their existing owners. Route insufficient proof to them with sanitized evidence instead of force-running a stale worker.

Expected acceptance before release: exact-head hosted focused suite, exact-head full regression, independent review, authorized serialized release slot, and a **real** after-merge read of live worker evidence. A passing test suite alone is not deployment or real generation proof.

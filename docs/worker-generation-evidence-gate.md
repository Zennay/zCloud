# Worker generation evidence gate (read-only)

The control plane must distinguish a **claim** (“Running” in Notion / portfolio queue)
from **observed generation**. A sent prompt, a saved worker assignment, a
self-reported worker status or an Actions job being queued are not evidence of
a generation that actually started.

## Invocation

```sh
python3 scripts/zcloud_worker_claim_evidence_gate.py evidence.json \
  --max-age-seconds 300 \\\n  --expected-assignment-id assignment-1 \\\n  --expected-worker-id worker-1
python3 -m unittest discover -s tests -p 'test_zcloud_worker_claim_evidence_gate.py'
```

Example JSON supplied by the **trusted on-host runtime evidence collector**:

```json
{
  "assignment_id": "assignment-1",
  "worker_id": "worker-1",
  "source": "trusted_runtime",
  "generation_started_at": "2026-10-09T02:58:00Z",
  "heartbeat_at": "2026-10-09T02:59:00Z"
}
```

The `source` field is **not authentication**. Never accept arbitrary client,
browser, worker-chat, or Notion JSON as trusted merely because it contains the
literal `trusted_runtime`. Provenance must be enforced by the calling collector
through a protected execution boundary, and the assignment/worker IDs must be
bound to the actual runtime observation before invoking this tool. This helper
does **not** perform that binding and must not be used as an authorization
decision for deployment, restart, job resumption, lease allocation, or queue
writes.

The helper returns deterministic JSON and exit code 0 only for fresh,
consistent runtime evidence. Missing fields, invalid or naive timestamps,
future heartbeats, out-of-order events and stale heartbeats fail closed.
No result authorizes mutations. A passing result is a narrow freshness
observation, **not** proof that the UI is currently streaming tokens, that
work is useful, or that an assignment completed successfully.

## Integration boundary

This is an independent evidence-format contract only. It does not modify
the serialized #580/#1089 writer/deploy gate, browser automation, SQLite,
Notion queue, service state, or production runner. Integrate only after a
dedicated owner supplies authenticated host-side evidence, freshness
semantics and exact-head verification.

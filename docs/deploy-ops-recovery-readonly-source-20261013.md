# Deploy-ops: missing-status retry admission — exact-main handoff

Scope: isolated reference for [issue #1216](https://github.com/Zennay/zCloud/issues/1216). The existing recovery workflow belongs to [PR #913](https://github.com/Zennay/zCloud/pull/913); no production, VPS, SQLite, browser or worker mutation from this branch.

## Actual GitHub evidence (13 October 2026)

Canonical REST combined status for `main@375ef9902aaf65076d7e659e753b774b96c19f91`: exact status SHA, `total_count=1`, `zcloud/vps-production=failure`, status ID `56253791511` (created `18:11:01Z`). This is an **existing FAILED production context**, not a missing status. Correct automatic result: `NOOP` with **`production_state=failure`** — never repeat scheduled regression as an undocumented failed-deploy retry. **NOOP is not a healthy deployment or green release.** Conflicting current production contexts fail closed rather than choosing a green status.

## Implemented reference

`scripts/zcloud_recovery_retry_policy_20261013.py`: `NOOP/WAIT/DENY/READY_FOR_EXCLUSIVE_RESERVATION`, **always** `authorized=false`. Checks production context first; no run-history pages needed to say NOOP for a verified failed status. A terminal exact-SHA deploy without production status DENIES rearm. Ambiguous/invalid evidence DENIES.

`scripts/zcloud_recovery_github_evidence_20261013.py`: canonical REST HTTPS **GET-only** observer with fixed repo ID `1389227570`, exact main SHA before/after, exact status count/SHA, same-repo workflow identity/path/event/branch, bounded complete 100-item pagination, duplicate-run rejection, bounded JSON and no redirects. Read-only status-present fast path uses three GETs: main, status, main. Sanitize all output. Never requests Actions dispatch, writes SQLite, restarts services, or changes tabs.

`reserve_reference()`: disposable SQLite `BEGIN IMMEDIATE` **test model only**; 16 concurrent same-key requests result in exactly one reservation. Hosted ephemeral SQLite is **NOT** a shared durable cross-run ledger. Ambiguous dispatch outcomes consume a reference reservation, not a retry ticket.

Hosted-only `.github/workflows/zcloud-recovery-retry-reference-20261013.yml`: run pure fixture tests, Python compile and independent GitHub-read observation; `contents:read`, `actions:read`, `statuses:read`, no VPS jobs, no write permissions. Never gives production authorization.

## Owner integration matrix

| Verified inputs | Automatic decision |
| --- | --- |
| Exact-main production status, **including failure** | NOOP |
| Missing/corrupt status or SHA drift | DENY |
| Current deploy/regression active | WAIT |
| Terminal exact-main deploy without status | DENY |
| No trusted green exact-main regression | WAIT |
| Absent status; complete snapshots; no deploy; trusted green regression | Candidate only, **authorized=false** |
| Same repo/SHA/workflow/intent already reserved | Deny duplicate |
| SHA changes | Revalidate from zero; no generic rearm |

## Remaining release gate

Workflow owner #913 must implement **durable, globally shared, atomic compare-and-reserve** before any live dispatch, with authenticated complete GitHub evidence and one-shot manual retries bound to actor/SHA/reason/expiry/nonce. Workflow concurrency is not a persistent deduplication store. Check #1135 privileged PR-trigger quarantine and serialized #580/PWQ-41 + #1089 before merging or deploying. Hosted CI is not evidence of working browser workers, production deployment or healthy VPS.

Offline commands:

```bash
python3 -m unittest discover -s tests -p 'test_zcloud_recovery_retry_policy_20261013.py' -v
python3 -m unittest discover -s tests -p 'test_zcloud_recovery_github_evidence_20261013.py' -v
python3 scripts/zcloud_recovery_github_evidence_20261013.py
```

The observation CLI exits 0 only for NOOP/WAIT and exits 2 on DENY or unapproved READY; neither outcome authorizes deploy/restart/redispatch.

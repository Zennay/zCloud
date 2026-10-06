# zCloud handoff completeness contract

Status: read-only control-plane foundation for the open roadmap rule:

> Handoff altijd bijwerken met: wat veranderde, bewijs, commit/PR, live status,
> volgende veilige taak.

This contract does not modify Notion, the queue writer, `server.py`, or the
dashboard. It audits the latest durable `project_state_receipts` row for every
active project and exposes only completeness state plus reason codes.

## Required facts

A latest handoff is `complete` only when it has all five facts:

1. **change_summary** — non-empty receipt `action`;
2. **proof** — valid, non-empty machine-readable evidence JSON;
3. **commit_or_pr** — a valid commit SHA or a recognized PR reference inside
   the evidence object;
4. **live_status** — a supported non-empty CI/runtime status;
5. **next_safe_task** — non-empty receipt `next_gate`.

Malformed evidence or an invalid/future observation timestamp makes the receipt
`invalid`. Missing facts make it `incomplete`. No receipt is `missing`.

## Privacy and safety

The report never emits action text, commit values, PR values, next-gate text or
evidence payloads. It returns only project id, receipt id, normalized
observation timestamp, state, and missing-field reason codes.

SQLite is opened with `mode=ro` and `PRAGMA query_only=ON`, and the audit
asserts `connection.total_changes == 0`. Database/project paths that are
symlinks are rejected. Active project IDs are bounded and must use the
canonical lowercase identifier grammar.

## Scope boundary

This proves handoff **content completeness**, not Notion-page freshness.
Existing receipt freshness logic remains authoritative for age/coverage. A
later serialized integration may use this contract to block “handoff complete”
claims or surface Attention Needed entries, but must not duplicate the receipt
writer or silently infer missing facts.

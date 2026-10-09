# Resource-lease snapshot integrity audit (offline, control-plane)

This is a **non-authorizing** auditor for zCloud's existing `project_runtime.resource_status()` read model.
It reads two exported UTF-8 JSON files (maximum 4 MiB each, bounded before parsing) and cannot open SQLite, call a service, dispatch a job,
repair a VPS, change a resource allocation, or authorize integration/deployment.

## Why this exists

The runtime returns both an aggregate `pools` map and a global `leases` list. A consumer that
trusts only one representation may incorrectly report spare compute or accept the wrong worker
as the owner. The existing `acquire_resource()` insertion also stores
`owner_id[:200]` while lookup and release use the untruncated caller value.
A persisted owner ID of exactly 200 characters therefore has ambiguous original provenance.

This snapshot verifier independently confirms all of the following before a *human review*:

- A bounded, timezone-aware snapshot timestamp, not future or older than five minutes. Caller-specified windows must be positive literal integers no greater than 300 seconds.
- Contract pool names and integer capacities match reported pool capacities.
- Every lease project exists in the contract registry and is allocated only to its contracted compute pool (no protected-pool spoof). Even idle projects must have valid, registered compute-pool bindings.
- Pool `used` / `available` counts are nonnegative integers and add to capacity.
- Every holder is represented exactly once in the global leases list, under the same pool, with identical lease times, CPU/memory/workload fields, and opaque metadata (compared but never printed).
- Lease identities are nonempty, have no surrounding whitespace or ASCII control characters, and are nonduplicated. Workload classes must be nonblank and lease/holder metadata must remain JSON objects.
- Every lease is active at snapshot time, not acquired in the future, with a positive lifetime.
- CPU/memory reservations are nonnegative finite numbers/integer MB; NaN/Infinity are rejected.
- The same (project, owner) cannot appear in two pools (matching the live SQLite primary key).
- Schema version must be a literal integer, not a boolean.
- Owner identifiers at the existing 200-character storage boundary are flagged for provenance review.
- No observed owner identifiers, metadata, tokens, or credentials are printed.

## Non-mutating invocation

```bash
python3 scripts/zcloud_resource_lease_snapshot_audit.py \
  --snapshot /path/to/exported-resource-status.json \
  --contracts project-contracts.json
```

For deterministic offline fixtures, add `--now 2026-10-09T17:00:00+00:00`.
Exit 0 means *only* that this JSON pair is self-consistent.
Exit 1 means malformed/contradictory/stale evidence. Output is stable, sorted JSON:

```json
{"errors":[],"lease_count":1,"mutation_performed":false,"ready_for_review":true,"safe_to_act":false}
```

No result, including `ready_for_review: true`, grants mutation authority.
Never run this checker from a privileged recovery job or use it to unblock
the #580/PWQ-41 + #576/#1089 serialized writer/deploy gate.

## Important limits of a normalized export

A green audit checks the *internal consistency* of a single exported JSON pair; it
does not prove it came from the production SQLite database, that the export was
complete, or that the producer faithfully represented raw stored fields. In
particular, existing `project_runtime._lease_payload()` silently substitutes an
empty metadata object if stored `resource_leases.metadata_json` is malformed. Thus
matching empty metadata in `holders` and `leases` can still pass the snapshot
check without proving raw SQLite JSON integrity. The source-level
`json.dumps(... )[:4000]` truncation risk is tracked separately in
[issue #1272](https://github.com/Zennay/zCloud/issues/1272).
The owner-ID truncation risk is tracked in
[issue #1271](https://github.com/Zennay/zCloud/issues/1271).

Future integration needs a trusted source identity, exact deployed commit SHA and
raw-storage validation **before** any automatic operational decision. The audit
does not create such evidence and always returns `safe_to_act: false`.

## Ownership and test evidence

- Separate, add-only control-plane scope: `scripts/zcloud_resource_lease_snapshot_audit.py`,
  `tests/test_zcloud_resource_lease_snapshot_audit_20261009_w1.py`, this document.
- Tests use synthetic dicts and temporary files. No live SQLite, browser, service, or job trigger.
- **Important:** `project_runtime.py` is already owned concurrently by draft #1270 and
  `fix/control-plane-runtime-capacity-receipt-freshness-20261009-w1`. Neither is modified.
- Only a future serialized owner may integrate snapshot auditing with a real service, after
  proving the supplied data is authentic and bound to the source and correct commit SHA.
- CI on a `worker/**` branch uses the repository's existing hosted smoke workflow;
  there is deliberately **no PR** while the main-branch dashboard recovery workflow still
  runs privileged `recover` on PR events (tracked by #1135/#1143).

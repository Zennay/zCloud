# Runner-command result fencing — integration handoff

Status: **observed defect, not fixed**. Scope: `/api/runner-command-result` in
`server.py`; owned by serialized #580/PWQ-41 + #576 integration lane.
This document and PR #1221 do **not** authorize server, worker or live database changes.

## Exact evidence

PR #1221 predecessor head `67724046360259a1f0db5175ab03b50d5110d303`
was tested by GitHub Actions regression run
[37859691920](https://github.com/Zennay/zCloud/actions/runs/37859691920):
644 tests, 2 failures, both in the source-contract file. The failures are:

* callback UPDATE lacks `AND status='pending'` (terminal replay may overwrite prior outcome);
* callback converts the command ID with `int(... or 0)` without positive-ID prevalidation.

CPU diagnostic 37859692047 succeeded; dashboard access recovery
37859691946 failed separately. None of this proves a live attack or a
successful server endpoint integration.

## Owner acceptance contract

1. Authenticate/authorize the callback as currently required; do not
   weaken the existing loopback restriction.
2. Validate integer command IDs, rejecting null, booleans, negatives, zero,
   malformed strings and out-of-range values with a controlled 4xx response.
3. Accept only `completed` and `failed` states.
4. Atomically update only a matching **pending** command; report non-match
   without changing rows or leaking whether an ID exists.
5. Keep result truncation and existing storage constraints. Decide the
   response status for stale/duplicate receipts explicitly (e.g. a bounded
   conflict, or idempotent non-mutating success).
6. Test duplicate same-status reports, opposite-status late reports,
   scheduler-staled commands, unknown IDs, and concurrent attempts against
   the actual endpoint, not only the in-memory reference model.
7. Once the production change is implemented, remove the two
   `@unittest.expectedFailure` annotations in
   `tests/test_runner_command_terminal_receipt_boundary_w4.py`, make both
   tests green, and add behavioral endpoint assertions for the HTTP contract.
8. Require exact-head regression and owner review before integration; do
   not bypass #580/#576 gates or deploy from this draft branch.

Current PR #1221 contains only add-only offline reference/contract checks
and this handoff. It does not establish production readiness.

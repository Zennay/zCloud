# Control-plane claim identifier equivalence — review contract

Tracking: [#1228](https://github.com/Zennay/zCloud/issues/1228). Status: **hypothesis / offline review only**. This document does not authorize production admission changes.

## Existing observable boundary

The README specifies a durable `(project_id, claim_key)` SQLite ownership key; `/api/task-claims` supports acquire, heartbeat, release and per-project list. Composite claim keys such as `notion:api` exist in tests. Distinct binary strings may not be the same *displayed* identifier, which matters when GitHub/Notion capability ownership is compared with HTTP/SQLite state. Do **not** silently normalize existing keys or collapse namespaces.

## Decision requirements before any enforcement

| Question | Required evidence | Fail-safe default |
| --- | --- | --- |
| Unicode NFC/NFKC vs literal strings | Compare current HTTP request validation, SQLite columns and equality, browser display, existing persisted rows | Keep literal values; no in-place rewrite |
| Mixed script and homoglyphs | Check visually confusable Latin/Cyrillic pairs in all claim surfaces | Surface exact escaped machine ID, not display-only ownership |
| Directionality and zero-width controls | Exercise U+202E, U+200D, U+2066 and a combining-mark pair | Reject only after compatibility analysis; flag in diagnostic evidence |
| Case and whitespace | Test leading/trailing whitespace and case-distinct identifiers | Do not case-fold without identifier schema owner approval |
| JSON decoding | Exercise escaped surrogates, null bytes, non-string and missing fields with an offline handler fixture | Must not generate a claim on invalid input |
| Delimiter and namespace | Exercise `cloud` vs `cloud::w1`, `notion:api` vs `notion：api`, distinct projects with same key | Never infer tenant/project ownership from visually similar strings |
| Length and log exposure | Oversized strings and escaped control content in list/diagnostic output | Bound and redact only after the response contract is mapped |

## Read-only acceptance procedure

1. First check current main, all open PR changed paths and unmerged PR-less branches. Do not take `tests/test_task_claims.py` or paths claimed by #1207 or #917.
2. Use an ephemeral SQLite file and local mocked handler only. No production `history.db`, management token, HTTP live endpoint, runner control, or queue write.
3. Obtain exact observed status/row counts for acquire, heartbeat, release, and GET list for each pair: `é` vs `e\u0301`; Latin `a` vs Cyrillic `а`; `x\u202Ey` vs `xy`; `notion:api` vs `notion：api`; case/whitespace variants; same key in separate projects; non-string and missing identifiers.
4. Verify non-owner release/heartbeat cannot change an existing valid owner, regardless of visual similarity. Verify logs, JSON and UI do not misrepresent raw identifier equivalence.
5. Record which differences are *intended* under existing rules. A failing test alone is not license to change normalization or migrate existing rows.
6. If a mismatch is proven, separately propose enforcement and migration gates and ask for owner review. Require exact-final-head tests and permanent-VPS proof of the isolated fixture before review-ready.

## Parallel ownership and release boundary

#1207 owns generic durable task-claim lease/concurrency tests. #917 owns integration with live worker preflight. #580 and #576 retain the serialized live writer/deploy gate. This contract owns only identifier-equivalence review and its future isolated fixture; **no live server, extension, SQLite, queue, deploy or workflow modifications**.

Outcome labels: `unverified`, `compatible_distinct`, `confirmed_confusion`, `needs_migration_design`. Do not report `confirmed_confusion` without exact repro evidence.
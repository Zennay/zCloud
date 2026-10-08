# Deploy status lookup: immutable candidate-SHA acceptance contract

Tracking: [#1160](https://github.com/Zennay/zCloud/issues/1160). This is an **offline, non-authorizing specification**. It neither claims ownership of `.github/workflows/zcloud-vps-deploy.yml` (currently #1089) nor changes its behavior. No production release, workflow dispatch, or service/SQLite mutation follows from this document.

## Trust boundary

An admission decision must never mix GitHub status for symbolic `main` at time T2 with a distinct candidate revision resolved at T1. Resolve `refs/heads/main` into canonical repository identity and validated full 40-hex SHA `S`; request status from `/commits/S/status` rather than `/commits/main/status`. Validate both HTTP success and status response `sha === S`, and confirm that a second canonical-main lookup after status evaluation still equals `S`. All incomplete, malformed, ambiguous, stale or conflicting evidence denies admission; neither automatic deploy nor already-green short-circuit is safe on unbound evidence.

## Minimal offline negative-test matrix

| Case | Input | Required decision |
| --- | --- | --- |
| Stable | canonical main = S before and after, response sha S, required status contexts valid | Evaluate existing status policy for S only |
| Moving main | main S before, T after | Deny; re-evaluate only in a new transaction |
| Missing status SHA | status response has no sha | Deny |
| Wrong status SHA | response sha T != S | Deny |
| Malformed candidate | canonical lookup empty, abbreviated, or not exactly 40 hex | Deny |
| Malformed response | missing statuses/context/state, wrong JSON shape, invalid HTTP result | Deny |
| Old green | latest green context belongs to T, even if S status unavailable | Deny; never skip write |
| API failure | any lookup timeout, error, or rate limit | Deny |
| Repository mismatch | lookup/status from fork or unexpected owner/repo | Deny |
| Parallel owner gate | #580/PWQ-41 or #1089 serialized owner not released | Deny production mutation, regardless of all SHA checks |

## Handoff / verification criteria

1. Obtain explicit handoff from current workflow owner (#1089) and refresh complete open PR **and PR-less branch** file overlap inventory.
2. Implement SHA-pinned lookup and compare-with-response validation in the owner-approved workflow scope; preserve subsequent prewrite freshness checks.
3. Prove all cases above with deterministic, mocked API fixtures (no network or privileged actions), including success-path compatibility.
4. Run exact-head hosted regression and bounded read-only VPS diagnostic on the final proposed commit.
5. Treat #580/PWQ-41 plus #1089 as active until both explicitly released; do not dispatch mutating jobs or interpret this document as approval.

This document is intentionally additive; the existing #1110 static-deploy-trigger test ownership and #1108 nine-path planner/preflight/intent ownership are not altered.

# HTTP readiness observations — non-authorizing reference (2026-10-09)

This document accompanies `scripts/zcloud_http_readiness_evidence_offline_20261009.py` and its isolated tests. It does **not** describe a deployed mechanism.

## Evidence contract
A single numeric HTTP status must be paired with independently observed service-active and SQLite-probe boolean evidence. Missing, malformed or non-boolean evidence is `incomplete`. An inactive service is `unavailable`; a failed SQLite probe is `degraded`. When both are positive, HTTP 2xx is `ready`, HTTP 502/503/504 is `transient`, and other statuses are `degraded`.

**No result grants restart, dispatch, merge, rollback or deployment authority.** In particular, HTTP 503 after a live service and green SQLite probe must not be rewritten as proof of service death.

## Production integration gate
These are offline classification semantics only. They do not verify observation source identity, ordering, freshness, atomicity or multi-sample stability. The production recovery owners (#581/#1143) and the serialized writer/deploy owners (#580/PWQ-41, #1089) must independently decide whether a future runtime implementation may consume the contract.

Do not use PR-origin code to perform recovery on a self-hosted runner. A trusted main-branch observation with independent health checks is needed before any operator recovery decision. This PR must remain draft until exact-final-head tests and independent ownership review.

# Transient API 503 is not dashboard recovery authority (2026-10-08)

**Scope:** offline acceptance fixture for deploy-ops / issue #1135. This document is a non-authorizing handoff; it changes no workflow, service, runner, database, browser, queue, or production state. Do not execute these sequences against production as a test.

## Known regression to preserve

The 2026-10-08 Notion checkpoint records external dashboard verification **SUCCESS** while privileged VPS `recover` failed in runs `37715959929` and `37710645579`. VPS evidence included an active service and healthy SQLite probe, followed by an unnecessary restart and a transient `/api/status` 503. A failing recovery job is **not** proof of an external outage. #581 owns healthy-service fast-path and API-readiness changes; #1143 owns quarantine workflow; #580/PWQ-41 plus #1089 own serialized production admission.

## Offline ordered event vectors

| Case | Ordered observations | Required decision | Prohibited side effects |
| --- | --- | --- | --- |
| H1 | external HTTP success; VPS service active; SQLite probe good; API ready | healthy no-op | restart, stop, sudo, file/database writes |
| H2 | external HTTP success; VPS service active; SQLite probe good; API 503 once; API recovers within bounded settle period | transient settling, no-op | restart/repair due to single 503 |
| H3 | external HTTP success; VPS service active; SQLite probe good; API 503 until timeout | ambiguous diagnostics only, fail closed | treating timeout as repair authorization |
| H4 | external failure; VPS service active; SQLite good; local API healthy | network/external ambiguity, fail closed | asserting VPS outage or restarting |
| H5 | external failure; VPS service inactive; local API unavailable; serialized gates open or unknown | deny live repair | privileged recovery or deploy |
| H6 | same as H5 with gates explicitly released, but run originated from untrusted pull_request | deny live repair | any privileged PR-triggered mutation |
| H7 | trusted exact-main event, gates released, verified unhealthy, then main SHA moves | stale identity, deny | repair based on prior SHA |
| H8 | duplicate webhook/retry attempt with prior non-authorizing success evidence | replay, deny new mutation | second restart or deployment |

All vectors must produce `mutation_performed=false` and `deploy_authorized=false` for the **untrusted or unavailable-authorization** states described above. Do not treat a green external check or a red internal recover job as permission to change a service.

## Owner implementation checks

1. Before any privileged branch, assert the trusted immutable event identity, checked-out SHA, actor, repository and *current* canonical main. A PR-originated event must exit without privileged commands.
2. Probe external reachability, service state, SQLite state and bounded local API readiness independently. Distinguish `503 during settling` from proven service failure.
3. Healthy service + good SQLite must use the no-op path without restart; repeated evaluation must remain idempotent.
4. For ambiguous evidence, write a bounded redacted diagnostic receipt only to non-privileged CI output, mark `recovery_authorized=false`, and stop.
5. Production recovery is separately admitted only after a complete current PR and PR-less ownership inventory, exact-final-head validations, independent review and released #580/PWQ-41 plus #1089 gates. No PR workflow approval is sufficient by itself.
6. Verify all negative paths with synthetic service/API sequences (no VPS changes), and capture exact run ID and SHA. Do not use this document as a test-run substitute or CI-green proof.

## Handoff and exclusion

#581 and the existing dashboard probe/recovery owners retain implementation and workflow paths. #1108 retains its nine paths. PWQ-258 belongs to Worker 2. This isolated documentation file is for review only; it does **not** claim ownership of another branch, grant deploy/recovery permission, or alter runtime admission.

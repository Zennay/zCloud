# Task-claims GET: observation is not necessarily mutation-free

Date: 2026-10-09. Scope: offline control-plane API contract review only; **no production or SQLite migration authorization**.

## Source contract

The documented `GET /api/task-claims?project=<id>` lists active claims and *automatically removes expired leases* (README, “Durable worker/task claims”). Therefore a caller must not infer that a successful GET is a purely observational or audit-preserving operation. It can update the durable claim table even without an explicit acquire/heartbeat/release request.

## Required distinctions for consumers

1. **Observation:** an HTTP 200 snapshot proves only what the authorized endpoint returned at the observation instant; it is not a durable ownership proof for subsequent action admission.
2. **Cleanup:** removal of expired leases is lifecycle housekeeping, not evidence that an identified worker explicitly released ownership. Do not emit an owner-release success receipt from this cleanup.
3. **No lease extension:** GET must not renew a claim; only the authorized current owner may heartbeat. A stale view must never grant execution authorization.
4. **Cross-project isolation:** filtering by project must never release or alter an unexpired claim in another project. A missing `project` parameter must not widen the caller's authorization.
5. **Transient failure:** timeout/503/invalid JSON means ownership is *unknown*, not “no active claims”; deny any claim-dependent execution pending authoritative reacquisition.
6. **Race safety:** GET followed by task execution is a TOCTOU pattern. Before execution, use the exact project/claim owner and unexpired lease proof from the canonical admission path.

## Offline acceptance matrix for future implementation owner

| Case | Fixture | Contractual assertion |
| --- | --- | --- |
| G1 | active A, expired B in project P | response includes A, omits B; no owner-release success receipt for B |
| G2 | active A in P, active C in Q | querying P does not modify C |
| G3 | current owner A then competing owner B | a prior GET cannot authorize A after B legally acquires |
| G4 | 503, malformed response or timeout | status unknown; no “claim free” decision |
| G5 | GET repeated during active lease | expires_at remains unchanged |
| G6 | no filter and unauthorized project | access remains under management authorization; no privilege expansion |
| G7 | concurrent expiry cleanup and acquire | one owner wins atomically; no false release of new owner |

## Ownership and integration gate

This ADR is a handoff for read-model semantics, not implementation. #1207 owns the isolated lease/concurrency test matrix, #1228 owns identifier equivalence, #917 owns claim-admission integration, and #580/PWQ-41 + #576 own serialized writer/integration gates. Their files are untouched. Before acting on G1–G7, inspect all paginated open PR changed paths and PR-less branches, assign one owner for each uncovered assertion, and obtain exact-head regression plus permanent-runner evidence. Keep production state, management tokens, queue, workers and deployment untouched.

# Deploy evidence time-boundary acceptance contract (offline, non-authorizing)

Status: **REVIEW ONLY — NO DEPLOY/RECOVERY/MUTATION AUTHORITY**. Created 2026-10-08. This document does not modify or execute any workflow.

## Purpose and ownership

Treat evidence timestamps as untrusted inputs until reconciled against a trusted clock and an immutable run identity. This add-only contract is independent of the existing artifact URL, archive-member, digest, provenance, content-type, PR-less pagination, and deployment/recovery implementation owners. It does not claim ownership of #1108, #1135, #580/PWQ-41, #1089 or Worker 2 PWQ-258.

## Proposed admission inputs (future implementation, not implemented here)

- trusted evaluation instant from the admitting server, recorded as UTC; fail closed if unavailable or clock health cannot be established.
- canonical repository and immutable commit SHA, workflow identity, run ID and attempt ID, artifact digest.
- source event completion timestamp and artifact creation/expiry timestamps, all explicitly timezone-qualified RFC 3339 strings.
- a documented, versioned maximum-age policy; missing policy denies admission.
- current serialized production ownership and release gate evidence, independently verified at *exact* current main.

## Mandatory invariants

1. Parse timestamps strictly; reject absent time zone, ambiguous local time, leap-second strings unless specifically supported, invalid dates, NaN/nonstring values, and duplicate/conflicting timestamp keys.
2. Normalize to UTC before any comparison. Do not use lexical comparison of differently-offset timestamp strings.
3. Require `created_at <= evaluated_at < expires_at` and `completed_at <= evaluated_at`. Future-origin timestamps deny admission, including small future drift until a documented tolerance policy exists.
4. Age is calculated against the trusted evaluation instant, not the caller's clock or a timestamp embedded in downloaded evidence.
5. Reject exactly-at-expiry evidence (`evaluated_at == expires_at`), expired evidence, negative/zero lifetimes and an absent or disabled maximum-age policy.
6. Preserve run ID + attempt ID: refreshed metadata or a rerun of the same run must not silently inherit a prior attempt's evidence window.
7. If evaluation crosses expiry, force a fresh evaluation before *any* authorized action; a prior passing screen never serves as a bearer permission.
8. A trusted clock failure, backward/forward discontinuity, unverified NTP health or inconsistent server clocks fails closed and produces a reason code without a side effect.
9. A passing timestamp screen is **only syntax and temporal consistency**, not provenance, authorization, deployment permission or recovery permission.
10. Independently require both serialized production gates #580/PWQ-41 and #1089 to be released on exact current main; this document cannot release them.

## Deterministic negative-case matrix

| Case | Example | Expected result |
|---|---|---|
| Missing offset | `2026-10-08T03:00:00` | DENY: AMBIGUOUS_TIMEZONE |
| Offset equivalence | `2026-10-08T05:00:00+02:00` vs `03:00:00Z` | Compare equal instants, never string order |
| Exactly expired | evaluate `03:00:00Z`; expire `03:00:00Z` | DENY: EXPIRED |
| Future evidence | created `03:01:00Z`; evaluate `03:00:00Z` | DENY: FUTURE_TIMESTAMP |
| Zero lifetime | created == expires | DENY: INVALID_LIFETIME |
| Inverted interval | expires < created | DENY: INVALID_LIFETIME |
| Stale completed run | completed earlier than configured max-age | DENY: STALE_EVIDENCE |
| Absent max-age policy | max-age unspecified | DENY: MISSING_POLICY |
| Run attempt changed | same run, new attempt | DENY until independent new-attempt proof |
| Clock unavailable | no trusted evaluation instant | DENY: CLOCK_UNTRUSTED |
| Clock discontinuity | trusted clock moves backward during admission | DENY: CLOCK_UNTRUSTED |
| Replay after screening | expiry passes before action | DENY: EXPIRED_ON_RECHECK |
| Wrong SHA, valid times | provenance SHA != exact main | DENY: IDENTITY_MISMATCH |
| Gate held, valid times | #580/PWQ-41 or #1089 not released | DENY: SERIALIZED_GATE_CLOSED |

## Handoff / test gate

An implementation owner may add pure deterministic parser/clock fixtures and exact-head CI tests after collision review. No passing fixture can authorize production action; integration must remain separate from this screen and explicitly prove zero mutation for all negative cases. Do not merge, dispatch, deploy, recover, restart services, mutate SQLite/queue, or promote evidence based on this contract. Record the exact tested SHA and independent review before considering any future change.

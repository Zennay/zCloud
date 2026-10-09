# Deploy-ops: bounded health observation decision table

Status: **offline acceptance input only — NOT ADMITTED**. This document does not grant restart, deploy, workflow-dispatch, merge, or production rights.

## Purpose and ownership

A successful external dashboard check and a failed internal `recover` job can coexist. A transient internal HTTP 503 is evidence for observation, **not** authorization to restart a service. See issue #1135 and existing workflow owner #581. This add-only document intentionally does not change their code. #1108 owns its nine planning paths; #580/PWQ-41 and #1089 own the serialized production gate. Do not bypass those owners.

## Observation-to-action matrix

| External dashboard | Internal API | SQLite | Privilege/trigger trust | Decision |
| --- | --- | --- | --- | --- |
| healthy | 200 | healthy | any | **NOOP**; success without recovery |
| healthy | one 503 | healthy | any | **OBSERVE**; bounded retry, no restart |
| healthy | repeated 503 | healthy | any | **ESCALATE**; collect redacted evidence, no automatic restart |
| healthy | any | unhealthy or unknown | any | **ESCALATE**; do not infer whole-site outage |
| unhealthy | 503 | healthy | pull_request or unknown | **DENY MUTATION**; external read-only verification only |
| unhealthy | 503 | healthy | trusted main but gate not released | **DENY MUTATION**; retain evidence for owner |
| unhealthy | repeated failure | unhealthy | trusted exact main, gate explicitly released | **OWNER REVIEW**; bounded transaction only after approved pre-change evidence |
| ambiguous/timeouts | unknown | unknown | any | **FAIL CLOSED**; no service changes |

**The table is intentionally conservative.** It is not a runtime algorithm, an exhaustive incident classifier, or evidence of healthy production.

## Required evidence before an implementing owner changes behavior

1. Record trusted event, actor, repository, immutable checked-out SHA, canonical main SHA, permanent runner identity, and confirmation that the candidate has not changed since the check.
2. Separate external HTTP/TLS evidence from internal API/SQLite checks. Keep status code, duration, retry count, check timestamp, and bounded error category; exclude tokens, cookie values, headers, raw payloads, database rows and process command lines.
3. Prove that every pull-request event takes a zero-mutation branch. In particular no sudo, systemctl stop/restart, chmod/chown/chattr, service drop-in write, database write, browser restart, or recovery dispatch.
4. Prove that any already-healthy external dashboard goes to NOOP on repeated executions.
5. Test temporary API 503 and probe timeout without treating either as permission for live recovery. Retry windows must be bounded and cannot erase the original failure evidence.
6. Re-check both #580/PWQ-41 and #1089 immediately before any proposed mutation. Open, stale, unknown or conflicting ownership is a hard DENY.
7. After *explicitly* serialized authorization, require snapshot, timeout, rollback path, exact deployed SHA and independently checked external receipt. If any proof is absent, fail closed.
8. Require exact-final-head full regression and security contract tests plus read-only VPS provenance checks. Previously green SHA evidence is not transferable to a changed head.

## Offline acceptance cases (no privileged execution)

- **A:** external 200, internal 200, healthy DB → NOOP; repeated input stays NOOP.
- **B:** external 200, internal 503 then 200, healthy DB → OBSERVE then NOOP; zero restarts.
- **C:** external 200, persistent internal 503, healthy DB → ESCALATE; never restart automatically.
- **D:** external 503, internal 503, untrusted PR → DENY MUTATION even if retries fail.
- **E:** all unhealthy, trusted SHA, unreleased #580 or #1089 → DENY MUTATION.
- **F:** external probe times out or status fields are missing → FAIL CLOSED.
- **G:** actor/repository/ref changed between initial and prewrite proof → DENY MUTATION.
- **H:** trusted, unhealthy, released and serialized but rollback snapshot fails → DENY MUTATION.

## Current release decision

**NOT ADMITTED.** The owner must publish exact head, exact-run CI links, independent read-only evidence, current gate snapshot and explicit production ownership before changing the decision. This document is a coordination artifact only; no production effect is claimed.

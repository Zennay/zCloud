# Dashboard recovery: trusted admission threat model (2026-10-08)

**Scope:** `zcloud-dashboard-access-recovery.yml` only. This is a review contract, not authority to run repair commands. See #1135 and quarantine PR #1143.

## Boundary

A GitHub Actions run triggered by an untrusted pull request must never obtain a self-hosted runner capable of mutating the production dashboard, even if the pull request itself changes no executable commands. A workflow-dispatch input, green hosted smoke check, or GitHub environment approval by itself does not grant release authority.

## Admission requirements before lifting the quarantine

1. **Immutable source:** resolve the candidate SHA server-side from `refs/heads/main` immediately before admission; require exact equality with the checked-out commit. No PR merge ref, attacker-controlled dispatch `ref`, or mutable branch passed through from an earlier job.
2. **Trusted trigger:** deny `pull_request`, `pull_request_target`, fork heads, reruns of untrusted events, and workflow-dispatch requests by default. Document a narrow trusted invocation that cannot inherit untrusted workflow content.
3. **Single flight:** serialize *all* production dashboard repair and deploy mutations through the existing production lock (PWQ-41 / #580). New concurrency groups must not bypass that lock.
4. **Bounded authority:** use the least-privileged runner and permissions possible; no write secrets or SSH material in hosted PR jobs. Privileged commands must only execute after admission succeeds and must not run when the dashboard is already healthy.
5. **Evidence:** persist an immutable receipt containing event, workflow/run attempt, actor, exact main SHA, checked-out SHA, approval/lock decision, health observations, outcome and timestamps. Redact tokens and credentials.
6. **Fail closed:** empty API response, timeout, 503, stale status, unavailable lock, missing approval, ambiguous SHA, or failed receipt write denies mutation. A failed check must not silently fall back to recovery.

## Reviewable negative cases

| Case | Required outcome |
| --- | --- |
| Pull request from repository branch | No privileged job admitted |
| Pull request from fork | No privileged job admitted |
| Manual dispatch against feature branch | No privileged job admitted |
| Main moves between verification and execution | No privileged job admitted |
| Concurrent production deploy already holds lock | Recovery does not run |
| Healthy API and public listener | No production mutation |
| Hosted verification only, no admitted repair | Hosted check reports diagnostics without escalation |
| Missing / malformed authorization receipt | No privileged job admitted |

## Handoff

- Keep #1143 draft and the privileged recovery job quarantined until an authorized owner has reviewed the exact-commit code and the shared-lock implementation.
- Run negative admission fixtures in hosted CI; verify exact-head checks separately. **Do not** validate by manually executing repair on production.
- After a serialized release, require a canonical VPS receipt under the same immutable SHA before considering the quarantine removable.
- This document creates no approval, dispatch, CI-green claim or production recovery permission.

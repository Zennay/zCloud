# PR-trigger recovery: reviewer dry-run cases

This is a **read-only review checklist**, not a workflow, command runner, approval token, or production test instruction. Never execute destructive probes against the live VPS. Reference: [#1135](https://github.com/Zennay/zCloud/issues/1135).

| Case | Event | Main SHA / input | Dashboard observation | Serialized gates | Expected result |
| --- | --- | --- | --- | --- | --- |
| PR-01 | `pull_request` fork | arbitrary | healthy | any | External verify only; zero self-hosted privileged recovery |
| PR-02 | `pull_request` same repository | exact-looking SHA | unhealthy | clear | External verify only; PR origin is still untrusted |
| PR-03 | `workflow_run` from PR check | success | unhealthy | clear | Deny escalation; upstream result cannot grant privilege |
| MAIN-01 | authenticated main | stale SHA | unhealthy | clear | Fail closed, no restart |
| MAIN-02 | authenticated exact main | current | healthy | clear | Repeatable success/no-op, unchanged service start time |
| MAIN-03 | authenticated exact main | current | isolated HTTP 503 | clear | Inconclusive/bounded retry; no restart |
| MAIN-04 | authenticated exact main | current | unhealthy | #580 unknown | Fail closed |
| MAIN-05 | authenticated exact main | current | unhealthy | #1089 open | Fail closed |
| MAIN-06 | authenticated exact main | current | unhealthy | clear | No mutation until independent owner release and rollback receipt |
| MAIN-07 | authenticated exact main | current | unhealthy | clear + explicitly authorized window | Only owner-controlled bounded action with post-check and rollback evidence |
| FAIL-01 | any | missing actor/repository/ref provenance | any | any | Fail closed before privileged runner path |
| FAIL-02 | any | ambiguous/mismatched run artifact SHA | any | any | Fail closed before privileged runner path |

## Evidence capture template

- Canonical base SHA (re-read at assessment):
- PR/commit head SHA:
- Workflow run URL and trigger event:
- Source repository, ref and actor identity (redacted as appropriate):
- External probe result and retry classification:
- Did any privileged job start? (must be **no** in PR cases):
- Before/after service start timestamps (healthy cases must match):
- Current #580/PWQ-41 and #1089 gate receipts:
- Test assertions and exact-final-head regression:
- Redacted no-mutation evidence:
- Owner disposition: **not admitted** unless independent production authorization exists.

Never put tokens, private URLs, browser session information, workflow secrets, access headers or raw service logs in a public PR comment. A successful read-only verify job is not equivalent to safe recovery or production release.

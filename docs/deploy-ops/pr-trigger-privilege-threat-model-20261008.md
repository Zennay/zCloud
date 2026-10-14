# Deploy-ops: privileged recovery trigger threat model

Date: 2026-10-08. **Non-authorizing evidence artifact.** This document does not approve a merge, deployment, restart, workflow dispatch, or privileged action.

## Security boundary

A GitHub `pull_request` event is an **untrusted input**, even when the changed workflow is stored in the same repository. A PR actor, head SHA, branch name, workflow artifact, and workflow-produced output must never grant the right to mutate the VPS. In particular, the self-hosted runner must not execute a PR-triggered privileged recovery job with a reachable restart/write path.

Issue [#1135](https://github.com/Zennay/zCloud/issues/1135) records the observed boundary violation risk; existing recovery workflow and audit branches retain ownership. This file does **not** prescribe edits to their workflow files.

## Minimum non-bypassable gates

| Trigger / evidence | Allowed behavior | Forbidden behavior |
| --- | --- | --- |
| Any `pull_request`, including same-repo PR | External, unprivileged, read-only health verification; redact output | `sudo`, `systemctl` restart/stop, config or filesystem ownership/mode edits, DB writes, Firefox/browser action |
| `workflow_run` with an untrusted source repository, actor, event or ref | Fail closed with a diagnostic | Promotion to trusted by a label, string match, branch naming, or a reusable workflow's own output |
| Trusted `main` workflow but SHA differs from current canonical `main` | Fail closed; re-evaluate from scratch | Acting on previously green checks or historical run IDs |
| Authenticated trusted exact-current-main, but external dashboard is healthy | Success/no-op with before/after verification | Entering the recovery branch or restarting a healthy service |
| Dashboard unavailable or transient HTTP 503 during settling | Bound probes, classify inconclusive, no-op and report | Treating a single transient failure as restart authorization |
| Trusted current-main, proven unhealthy, live serialized gate still open/unknown | Read-only diagnosis, no-op | Repair, deploy, queue/runner/browser/service mutation |
| Trusted current-main, proven unhealthy, gates clear, explicit owner and rollback evidence present | Owner-controlled bounded recovery under separate authorization | Automatically inheriting authorization from this document or a PR check |

## Evidence required for admission

1. Record canonical `main` SHA, trusted trigger provenance, workflow identity, event type, actor, run ID, and immutable artifact/check identities.
2. Demonstrate a PR-trigger negative test with zero privileged commands and zero filesystem/service/database/browser writes, including malformed ref, actor, or repository cases.
3. Demonstrate healthy-dashboard short-circuit twice without service restart and with matching service start time.
4. Demonstrate failure closed for SHA drift, open/unknown #580/PWQ-41 or #1089 gate state, transient HTTP 503, and missing privilege.
5. Demonstrate only on an independently authorized maintenance window: proven unhealthy condition, pre-change snapshot, timeout, limited action, rollback, redacted post-change receipt.
6. Re-run exact-final-head regression/security suite and read-only VPS verification. An external verification job passing while a recover job fails is **not** a passing recovery gate.

## Boundary of this artifact

The workflow owner must implement and test the policy in its own files. Existing #581, #1108, PWQ-258 and serialized #580/PWQ-41 + #1089 ownership remain undisturbed. The source-of-truth for production approval remains the separately controlled release gate; this document cannot make a run eligible.

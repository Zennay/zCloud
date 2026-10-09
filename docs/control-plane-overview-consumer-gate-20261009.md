# Control-plane overview consumer admission — 2026-10-09

This is a review-only handoff for [#789](https://github.com/Zennay/zCloud/issues/789). It does **not** grant merge, runtime-write, production-deploy, queue or recovery authority.

## Ownership and dependency gates

| Consumer | Existing authoritative foundation | Admission requirement |
| --- | --- | --- |
| Portfolio overview | #782 | Verify foundation landed on current main; reuse projection, do not implement another query or writer. |
| Runner-command outcomes | #787 | Display measured action success/failure ratios only as operational telemetry, not a project quality score. |
| Event activity | #776 / #777 | Defer central eventstream wiring until event-schema #614 and timeline #609 ownership has cleared. |
| Dashboard placement | #789 | Inspect all active dashboard PRs and PR-less branches immediately before editing shared files. |
| Read-only API | #789 | Hold `server.py` until serialized writer #580/PWQ-41 and #1089 release their window. |

## Fail-closed acceptance checklist

1. Freeze a precise main SHA and list **all pages** of open PRs plus PR-less branches, with changed-path comparison; unknown or incomplete ownership evidence means **stop**.
2. Confirm foundation output shapes at that SHA. Missing, stale, malformed or exception-producing projectors must render `unavailable` (not zero, healthy or success).
3. API consumer is GET-only; reading telemetry must never acquire claims, trigger workflow dispatch, restart workers, write SQLite, alter project layout, or mutate release state.
4. Apply strict output allowlisting: bounded aggregate counts/rates and named state enums only. Exclude raw task/queue/attention IDs, prompts, conversation IDs, command payloads, error strings, arbitrary receipts and tokens.
5. Dashboard first layer prioritizes running projects, blocked/attention states and next available action. Operational rates and resource detail belong behind disclosure and retain explicit timestamp/staleness metadata.
6. Negative tests cover unauthenticated reads as permitted by the existing endpoint policy, forged or oversized projection output, missing timestamps, stale samples, zero denominators, partial evidence and conflicting active owner paths. Do not quietly change the endpoint's current security model.
7. Exact final-PR-head validation must include focused API/UI tests, normal zCloud regression and read-only permanent-VPS smoke. Record immutable run IDs, head SHA, and outcome. Green on another branch or previous head is not acceptance.
8. Recheck main movement and serialized ownership immediately before integration. No merge/deploy while #580/#1089 remain owned or unresolved.

## Ownership collision response

If any candidate path has an owner, do not fork an overlapping implementation or force-push another worker's branch. Attach the missing case to #789, preserve the existing owner's branch and work on a demonstrably file-disjoint slice. This document itself is a non-authorizing design/acceptance artifact; no integration completion is claimed.

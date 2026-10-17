# zCloud control-plane ownership and evidence handoff — 2026-10-09 03:50 UTC

This is a **non-authorizing, point-in-time handoff**, not a live worker/runner health report. Never treat a Notion Running claim, queued GitHub check, or successful check on another SHA as proof of current generation, deployment, or permission to mutate production.

## Canonical pointers

- [zCloud project](https://app.notion.com/p/3e79e19ac955811d8fd4d35d176bdeb8) has posture `Accelerate`; [Current State & AI Handoff](https://app.notion.com/p/3e79e19ac955819e9ccee5bec98bbb9c) places completion in `Verifying`.
- The 2026-10-08 22:07 UTC Notion handoff cited historical `main@b8bd5684`; the fresh PR creation on 2026-10-09 03:50 UTC instead resolved `main@900f9b95510e1bcf31ec99690b728d572d1ec416`. Never treat the historical audit SHA as current. **Re-read exact main before each operation.**
- Serialized integration belongs to [#580](https://github.com/Zennay/zCloud/pull/580) / PWQ-41; successor deployment gate is [#1089](https://github.com/Zennay/zCloud/pull/1089). Their review/merge windows may change; a separate worker must recheck them.
- [#1034](https://github.com/Zennay/zCloud/pull/1034) owns cross-repository Actions queue pressure; [#1143](https://github.com/Zennay/zCloud/pull/1143) owns fail-closed recovery. No second owner should dispatch, restart, or edit their live files.

## Snapshot and interpretation

- The 2026-10-08 22:07 UTC audit counted 3 Running, 32 Verifying, 7 Queued, 5 Blocked, 243 Done, 16 Dropped in Notion; these are administrative labels, not authenticated runtime signals.
- That audit found 116 queued Actions runs across the portfolio. A later decline alone cannot prove capacity recovery.
- The audit did **not** capture fresh zCloud listener, SQLite heartbeat, Firefox generation, CPU/RAM/swap, or current runner state. Do not extrapolate from earlier green checks.
- A 2026-10-09 GitHub search lists independent draft/offline control-plane slices [#1231](https://github.com/Zennay/zCloud/pull/1231) redaction, [#1233](https://github.com/Zennay/zCloud/pull/1233) causal ordering, [#1238](https://github.com/Zennay/zCloud/pull/1238) sequence gaps, [#1240](https://github.com/Zennay/zCloud/pull/1240) generation evidence, [#1242](https://github.com/Zennay/zCloud/pull/1242) timezone freshness, [#1243](https://github.com/Zennay/zCloud/pull/1243) contradictory sources, [#1244](https://github.com/Zennay/zCloud/pull/1244) identity collisions. Their own exact-final-head tests/reviews remain distinct gates.

## Evidence-driven triage, in priority order

1. **Recheck ownership and immutable identities:** read exact current main, open PR files, branch ownership, workflow run + attempt and expected runner/environment before proposing changes. A historical PR body is not proof that its head is green now.
2. **Run only already trusted main-branch read-only diagnostics** through the existing permanent-VPS route when an approved owner has identified them. Do not dispatch untrusted PR code on the VPS. Inspect the listener/service, SQLite queue and live generation heartbeat separately; redact prompt/task content.
3. **Classify the failure without manufacturing success:** no heartbeat ≠ stopped browser, queued Actions ≠ dead runner, green repo CI ≠ production deployed, GitHub connector API limitations ≠ inaccessible VPS.
4. **Keep the serialized write window:** send actual fault evidence to the owner of #580/#1089 or the specific owner of #1034/#1143. No claim-release, global worker refresh, restart, merge, rollback, deployment or queue mutation solely from this document.
5. **Before promotion:** independently verify all required checks on the *exact final head*, an owner-approved source diff and serialized gate release. If anything is unknown, status remains *unverified*.

## Handoff record to fill for a new observation

```text
observed_at_utc:
repo/main_sha:
pr_number/head_sha/base_sha:
workflow_run_id/attempt/job_id:
runner_identity/environment:
service_status_observed_at:
sqlite_queue_observed_at:
worker_generation_id/heartbeat_observed_at:
source_of_each_fact:
current_owner_and_gate:
redacted_failure_reason:
requested_read_only_followup:
production_mutation_performed: false
authority_granted: false
```

Do not paste tokens, prompt text, credentials, private conversation identifiers or raw task payloads into the handoff. This file is only an operational evidence reference; it does not execute checks or change the control plane.

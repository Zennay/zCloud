# Deploy-ops: missing-status retry admission — exact-main handoff

Scope: isolated reference for [issue #1216](https://github.com/Zennay/zCloud/issues/1216). The existing recovery workflow belongs to [PR #913](https://github.com/Zennay/zCloud/pull/913); no production, VPS, SQLite, browser or worker mutation from this branch.

## Actual GitHub evidence (13 October 2026)

Canonical REST combined status for `main@375ef9902aaf65076d7e659e753b774b96c19f91`: exact status SHA, `total_count=1`, `zcloud/vps-production=failure`, status ID `56253791511` (created `18:11:01Z`). This is an **existing FAILED production context**, not a missing status. Correct automatic result: `NOOP` with **`production_state=failure`** — never repeat scheduled regression as an undocumented failed-deploy retry. **NOOP is not a healthy deployment or green release.** Conflicting current production contexts fail closed rather than choosing a green status.

## Implemented reference

`scripts/zcloud_recovery_retry_policy_20261013.py`: `NOOP/WAIT/DENY/READY_FOR_EXCLUSIVE_RESERVATION`, **always** `authorized=false`. Checks production context first; no run-history pages needed to say NOOP for a verified failed status. A terminal exact-SHA deploy without production status DENIES rearm. Ambiguous/invalid evidence DENIES.

`scripts/zcloud_recovery_github_evidence_20261013.py`: canonical REST HTTPS **GET-only** observer with fixed repo ID `1389227570`, exact main SHA before/after, exact status count/SHA, same-repo workflow identity/path/event/branch, bounded complete 100-item pagination, duplicate-run rejection, bounded JSON and no redirects. Read-only status-present fast path uses three GETs: main, status, main. Sanitize all output. Never requests Actions dispatch, writes SQLite, restarts services, or changes tabs.

`reserve_reference()`: disposable SQLite `BEGIN IMMEDIATE` **test model only**; 16 concurrent same-key requests result in exactly one reservation. Hosted ephemeral SQLite is **NOT** a shared durable cross-run ledger. Ambiguous dispatch outcomes consume a reference reservation, not a retry ticket.

Hosted-only `.github/workflows/zcloud-recovery-retry-reference-20261013.yml`: run pure fixture tests, Python compile and independent GitHub-read observation; `contents:read`, `actions:read`, `statuses:read`, no VPS jobs, no write permissions. Never gives production authorization.

## Owner integration matrix

| Verified inputs | Automatic decision |
| --- | --- |
| Exact-main production status, **including failure** | NOOP |
| Missing/corrupt status or SHA drift | DENY |
| Current deploy/regression active | WAIT |
| Terminal exact-main deploy without status | DENY |
| No trusted green exact-main regression | WAIT |
| Absent status; complete snapshots; no deploy; trusted green regression | Candidate only, **authorized=false** |
| Same repo/SHA/workflow/intent already reserved | Deny duplicate |
| SHA changes | Revalidate from zero; no generic rearm |

## Remaining release gate

Workflow owner #913 must implement **durable, globally shared, atomic compare-and-reserve** before any live dispatch, with authenticated complete GitHub evidence and one-shot manual retries bound to actor/SHA/reason/expiry/nonce. Workflow concurrency is not a persistent deduplication store. Check #1135 privileged PR-trigger quarantine and serialized #580/PWQ-41 + #1089 before merging or deploying. Hosted CI is not evidence of working browser workers, production deployment or healthy VPS.

Offline commands:

```bash
python3 -m unittest discover -s tests -p 'test_zcloud_recovery_retry_policy_20261013.py' -v
python3 -m unittest discover -s tests -p 'test_zcloud_recovery_github_evidence_20261013.py' -v
python3 scripts/zcloud_recovery_github_evidence_20261013.py
```

The observation CLI exits 0 only for NOOP/WAIT and exits 2 on DENY or unapproved READY; neither outcome authorizes deploy/restart/redispatch.

## Shared idempotency alternative — GitHub create-only tag refs (13 October)

New implementation: scripts/zcloud_recovery_git_ref_ledger_20261013.py and tests/test_zcloud_recovery_git_ref_ledger_20261013.py.

This OFFLINE reference replaces the *conceptual* disposable SQLite claim with a **durable repository-scoped create-only Git reference plan** for an owner's evaluation. The GitHub REST Create a reference endpoint POST /repos/Zennay/zCloud/git/refs can point a lightweight tag to an existing main commit. A ref named refs/tags/zcloud-recovery-reserved-v1/regression/<exact-40-hex-main-sha> is deterministic across independently started GitHub-hosted runs; an attempted second create cannot create a second ref with the same name. **No actual Git ref has been created, read or deleted by this reference or its CI.**

Source-only reservation_plan requires the pure classifier's READY_FOR_EXCLUSIVE_RESERVATION with authorized=false, canonical SHA, repository and fixed ref prefix. classify_create_result considers only HTTP 201 plus an exact matching ref, object type commit and SHA a valid *reservation recorded*; the result ALWAYS has authorized=false and dispatch_allowed=false. 409/422 are ambiguous errors (validation, uniqueness or spam), never duplicate-success. 401/403, redirects, 5xx, timeout or malformed 201 response never allow automatic retry. classify_existing_ref can report a matching pre-existing ref to an operator but cannot prove which request created it. Mock server concurrence verifies one winner among 64 requests, and a new commit SHA produces a new key; this does **not** prove server behavior or permission applicability on GitHub itself.

Owner integration requirements: confirm repository rules/tag protection and GitHub API semantics under controlled approval, use **contents:write only in the specifically guarded owner workflow**, keep hosted probes contents/actions/statuses:read, pin exact current main and trusted event provenance, perform status/main revalidation before and after claim and prior to any privileged action; maintain policy distinguishing reservation from dispatch authorization. Never delete, force-update, overwrite or retag a reservation automatically. Branch/tag deletion or forced updates by privileged users are outside the proof boundary: protect the dedicated tag prefix or choose another managed durable store if needed. Do not claim GitHub ref creation has been exercised in production; integration remains behind PR #913 / #580 / #1089 / #1135. Git refs have retention/namespace cost and should be reviewed before adopting them as a production ledger.

GitHub endpoint reference: https://docs.github.com/en/rest/git/refs#create-a-reference (requires Contents write; create returns 201 and may return 409/422). No code here issues a POST.

## Repository protection observation — admission remains DENIED

Read-only GitHub REST GET /repos/Zennay/zCloud/rulesets returned [] during this run (13 Oct 2026). This observation does NOT prove any required tag-namespace protection is installed. Traditional/other tag protection and actual effective repository-rule evaluation have not been fully audited. Consequently the tag-backed approach is **not approved for production** and is no substitute for the owner's independent authority/retention review.

The reservation plan now explicitly requires protected tag namespace, no delete/force rewrite, owner provenance, exact-main checks before and after any hypothetical claim, and durable owner review. The result classifier rejects plans with any of those conditions removed. These are contract flags, **not verified attestations**: returning true in a plan does not prove GitHub configuration is true. Owner #913 must verify effective protection or choose a different durable backend before invoking anything with contents:write. No Git tags have been created or modified; a canonical read-only GET of the proposed current-main tag ref returned 404.

**Receipt truthfulness:** the offline response classifier now reports `local_mutations_performed=false` separately from `external_ref_creation_observed=true` only for a supplied, matching GitHub 201 response. A mock/transport classification is not proof that this assistant or its CI actually wrote Git refs. Existing-ref reconciliation explicitly reports `external_ref_creation_observed=false`; it can observe another actor's prior reservation but cannot claim creation ownership.

## Verified ruleset prerequisite: exact namespace and no bypass (continued deploy-ops)

The add-only `scripts/zcloud_recovery_tag_ruleset_preflight_20261013.py` (and own offline tests) screen **canonical GitHub REST GET** rulesets with parents included, `targets=tag`, bounded complete list pagination and per-ID rule details. It binds the repository identity (`Zennay/zCloud`, immutable repository ID `1389227570`) and verifies current-main SHA did not move mid-read.

A candidate can be reported only for an **active tag ruleset**, exact dedicated prefix `refs/tags/zcloud-recovery-reserved-v1/regression/*`, no exclusions, **exactly** the `update` and `deletion` rules, and an explicit empty `bypass_actors` array; extra conditions, hidden bypass information, ambiguous identities and incomplete pagination all fail closed. A ruleset with a `creation` rule also fails this proposal because initial reservation cannot be created without a separate bypass design. GitHub's read-only API may hide `bypass_actors` from a token without adequate ruleset access; **missing means unknown, not no bypass**. The owner must verify effective protections and who can mutate/delete the ruleset itself. Even matching syntax returns `effective_protection_verified=false`, `owner_review_required=true`, `authorized=false`, `dispatch_allowed=false`.

In a live **read-only** GitHub-hosted action on 13 October 2026, the endpoint returned an empty applicable ruleset inventory (`reason=no_rulesets`) and `tag_protection_candidate=false`; no ref was created. GitHub main independently advanced to `2385302f71862dfd903b3c780ab988bdc9b9ddb3` from an unrelated zBrowse incident workflow commit. The new exact-main combined production status at the 21:24 UTC observation was `pending` (formerly `failure` on prior main SHA). The independent production observer emitted `NOOP`, `production_state=pending`, `authorized=false`; **pending does not mean deployed/healthy**. No attempt was made to rearm the workflow.

This is an independent fail-closed design screen only. It does not verify effective or tamperproof policy, create/change repo rules, reserve a ref, release a deploy, or replace PR #913's exclusive integration owner. The correct response to a missing ruleset is an **owner gate**: establish actual protected, immutable reservation storage or select a different durable backend; never infer eligibility from a mocked one-winner race.

## Manual one-shot retry rearm: cryptographic *candidate only*

New add-only `scripts/zcloud_recovery_manual_retry_proof_20261013.py` and focused tests implement a pure, offline HMAC-SHA256 screening contract for **explicitly authorized human retry proposals**. It solves neither GitHub workflow caller authentication nor production dispatch: both remain owned by PR #913 and serialized #580/PWQ-41/#1089.

The envelope binds **canonical repository, exact 40-hex main SHA, `rearm_regression_once` action, independently authenticated actor, bounded operator reason, UUIDv4 nonce, issued-at and <=10-minute expiry**. It rejects mixed JSON types, unknown fields, altered signature, forged actor, changed SHA, future/expired proof and weak key. The signing secret is supplied by the owner context in tests; it is never read from the repository or exported. A real workflow owner must keep the HMAC secret in an authorized confidential store, independently authenticate actors (never from envelope claims alone), and protect it against workflows triggered by untrusted PRs.

Even a correctly signed envelope yields ONLY `READY_FOR_MANUAL_NONCE_RESERVATION`, `authorized=false`, `dispatch_allowed=false`, and `requires_atomic_shared_claim=true`; it is **not** a green deployment, an approved GitHub dispatch or an exception to the current production owner gate. A successful offline signature remains replayable until the owner atomically reserves `manual/<sha>/<nonce>` in protected **shared durable** storage. Tests simulate 64 concurrent claims with one winner; production store integrity, actor/owner authorization and exact-main recheck are still unproven. Failed/uncertain claim outcomes should be terminal for automatic retries, not an opportunity to reuse the nonce.

Do not run manual retry while live `zcloud/vps-production` is pending or unresolved, while GitHub tag namespace immutability is unverified, or while owner workflow #913 remains unintegrated. The tool does not create tags, send HTTP requests, perform actions dispatch, restart workers, or write VPS/SQLite.

### Ruleset inventory race guard

The read-only ruleset observer now performs a **second complete bounded list** after fetching each ruleset's details, and requires byte-equivalent parsed list evidence. A ruleset that disappears or whose enforcement/identity changes during the observation is rejected with `rulesets_changed_during_snapshot`. This is a conservative instability tripwire, **not an atomic repository-rules transaction**; it cannot prove no administrator ever changed a rule between reads or establish lasting tag immutability. The only valid use is a fail-closed source hint for the exclusive owner.

Snapshot dated 13 October 2026: GitHub's main advanced to `96f05fe86882f8a63aba831bb02578b67dd536b4`; no exact-main combined production status was recorded at the last REST read, while the independent trusted run observer saw an active exact-main deployment and returned `WAIT / deploy_active / authorized=false`. This is **not** proof that deployment finished, and no manual retry may be attempted from this branch.

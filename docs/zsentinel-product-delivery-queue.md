# zSentinel product-only queue admission (integrated 18 October)

The portfolio queue's ordinary **new** worker admission now skips isolated QA, regression-test and audit-only work packages while still admitting actual product bugs, feature implementation, integrations, merges, and recovery tasks.

* Live eligibility, priority P0/P1, resource limits, manual-start preference, independent project lane/capability conflicts, reviewer gates and exclusive lease/claim rules remain unchanged.
* Already `claimed`, `running` or `verifying` work is **never cancelled, displaced or rewritten** by this feature. Its scope is included in the lane ownership graph even when a queued QA item is skipped.
* Metadata `lane`, `lane_id`, `focus`, `workstream` etc matching known QA-only lane IDs denies **new** admission; explicit QA/test/validation title prefixes also deny **unless the ticket requirements explicitly demand a real fix/build/delivery**. A P0 item called `Test urgent planner navigation` with a `Fix ...` completion criterion remains a P0 product fix; a pure expected-RED test carrier remains excluded.
* A legacy neutral-title task containing only a QA/regression-suite instruction with no actual delivery verbs is not freshly claimed. That task remains in the queue for existing owner-authorized re-scoping; no fake `done` or de-eligibility is written to SQLite.
* Lane selection sees only admissible queued candidates, so a high-priority old QA item does **not** hide the next buildable product item in the same lane. Active work is still visible for cross-lane scope protection.
* This product change is integrated into the unified zSentinel backend PR #1549 as `server.py` + `scripts/zcloud_product_delivery_policy.py`. No VPS, worker, DB or file data has been changed. It becomes operational **only after** the regular serialized production deployment and runner #1326 are actually restored and verified. All permanent existing CI and reviewer/approval gates still apply; this change adds no QA/test suite or synthetic fixture.

If the queue contains no eligible product item, leave workers idle rather than inventing a test round or taking over another worker's claim. Queue items remain auditable for manual owner review.

Admission scans at most four ordered 240-row queue pages (960 queued rows in total) to find up to 240 viable product candidates. This prevents a large stale QA backlog in the earliest priority page from hiding a runnable product ticket; the scan is read-only and bounded, with no scan beyond 960 rows automatically assumed complete.

The existing authorized `GET /api/portfolio-queue` now also exposes `delivery_policy` with queued QA-only held count, a bounded count and up to eight P0-first advisory product candidate IDs, explicitly flagged `not_claimed_or_authorized=true` and `admission_scope_lease_and_capacity_not_verified=true`. This is read-only, additive and does not modify the existing queue `items`, lease, route authorization or allocation response contracts.

Unparseable, duplicate-key, non-object or overlong workstream metadata now fails closed for **new** admissions, preventing an ambiguous serialized `qa-only` classification from being silently converted to product work. Existing active claims and historical queue records are never rewritten.

## Browser composer recovery operator evidence (zSentinel #698)

The same read-only worker-progress API now admits exactly three additional browser event **kinds** (`send-blocked`, `composer-restored`, `conversation-reset-requested`) and a **fixed allowlist** of composer-recovery reason codes. Raw browser-supplied `reason`, `error`, conversation text, tokens and URLs are never returned by this projection. Old SQLite installations without a `reason` column continue to be read through `NULL AS reason`, without schema writes or migrations.

The Activity UI in [#1550](https://github.com/Zennay/zCloud/pull/1550) renders an operator instruction only for these exact allowlisted codes; the actual v2 one-shot composer repair is a separate source feature [#1552](https://github.com/Zennay/zCloud/pull/1552). None of the three sources proves a new active owner, production deployment or that generation resumed. All require protected-main review and serialized release before they work together.

## On-demand canonical GitHub Activity and P0/P1 source APIs

The backend now reuses the original **existing read-only** provider clients from [#1538](https://github.com/Zennay/zCloud/pull/1538) and [#1539](https://github.com/Zennay/zCloud/pull/1539), copied byte-identically to backend PR #1549 to avoid requiring a missing module if the backend is integrated ahead of the dashboard.

- `GET /api/github-activity-live?project=<id>`: 48-hour, max-100-record bounded PR/CI/main observation for a canonical repository. Source clock, observation clock, partial/truncated coverage and independent PR/merge/main proof are kept distinct.
- `GET /api/product-issues-live?project=<id>`: existing bounded older/newer open issues and open PR references, max-90-record P0/P1-first inbox with QA-only records suppressed. A mention does **not** prove ownership.
- Only a canonical `projects.json` project ID is accepted, never an arbitrary URL/repository. The existing `action_request_allowed` trusted peer fence protects both endpoints; external unauthorized clients receive 403 before provider work begins.
- Both APIs are **opt-in**, never scheduled. A singleflight in-memory pool permits at most two concurrent provider observations, max sixteen cached project/source combinations and five-minute TTL. The shared anonymous GitHub request budget additionally reserves worst-case source calls, capped at **45 provider requests per rolling hour**, returning `rate_budget_wait` with a bounded retry estimate rather than hammering a provider denial or reporting zero issues. A request returns `state=refreshing` until the bounded observation finishes; `state=partial` only with a valid non-authorizing receipt. Provider denial, private repository or timeout is `unavailable` — not a verified empty feed. Over capacity returns `capacity_wait`.
- Public anonymous GitHub GET only: **no GitHub token is read or forwarded**, no API response body or freeform upstream error escapes an unavailable envelope; remote redirects are denied by the reused provider readers. Private repos remain unavailable unless a separate reviewed read-only authorization mechanism is implemented.
- No SQLite write, claim/lease mutation, Actions workflow dispatch, browser auto-poll, source completeness or deployment proof is implied by these endpoints.

The Activity UI in [#1550](https://github.com/Zennay/zCloud/pull/1550) exposes **Read VPS GitHub** and **Read VPS issues** as distinct manual buttons, with existing public-browser GET and explicit JSON import left as independent fallbacks. The page validates both provider receipt formats and holds source-specific error/partial/age indicators; API success never means product shipped. Approval and actual VPS runtime deployment are separate existing gates.

## Queue lane correctness with existing active writers (#1268)

The original owner source at `fix/control-plane-lane-rejection-fallback-20261009-w1` demonstrated two actual planner defects that could mislead zSentinel's **product-first** admission:

1. A rejected higher-priority candidate left `blocked_by` entries in a lane **even after** a later non-overlapping queued fallback was safely selected. The selected task incorrectly appeared blocked, encouraging repeated work on an already-resolved dependency. `lane_generator.generate_execution_lanes()` now clears the prior rejected-candidate conflict list only when a safe candidate has been chosen; fully blocked lanes still retain their genuine blockers.
2. When more than one task in a lane was already `claimed`, `running` or `verifying`, only the first was entered into `occupied_scopes`. The read-model now reserves **all** active items' declared write scopes before admitting any other lane. One active item remains the display representative, but hidden active writers cannot be silently ignored.

These are precisely scoped source integrations on the new runtime branch, not edits to the original owner branch and not a modification of an active queue/claim. The older experimental strict-scope/metadata parser, stale source tests and unrelated lane work were **not** copied. The source cannot be pronounced live until protected-main review, mandatory exact-head CI and serialized release have completed.

## Separate QA-only admission from malformed product work

An operator-read `delivery_policy` report previously lumped *all* rejected queued candidates into `queued_qa_only_held`. That misrepresented a genuinely important P0/P1 product task with invalid metadata, missing/oversized title, or oversized criteria as an intentional QA-only skip, making the actual actionable blocker invisible.

The actual API now calculates that advisory report against the same raw SQLite result set **before** the public row normalizer discards `metadata_json`. The caller receives separately sanitized `items` and the source-faithful `delivery_policy` from one read snapshot; no raw metadata is returned and no second potentially torn queue read is introduced. Other callers of `portfolio_queue_items()` keep the exact former list return contract.

The product admission predicate **is unchanged**. Instead, `held_queue_review_reason()` classifies non-admitted, eligible queued entries for the read model only:

- `qa_only`: actually falls into the existing QA-only classification, so it remains in `queued_qa_only_held`.
- `invalid_metadata`, `invalid_title`, or `oversized_criteria`: counted in a distinct `queued_requires_owner_review`, with up to eight bounded `review_items` identifying project, priority and constant reason. The row is **not claimed, assigned or auto-repaired**.

The operator UI in PR #1550 now displays these separately; the exported Markdown handoff carries only project, priority, reason and aggregate count (not raw queue IDs). Existing claimed/running/verifying records, ACL, lease, scope, quota and mandatory CI/release gates remain intact. This is a source-visible product unblocking aid, not verified production queue remediation.

## On-demand queue retry incidents (#677; source 19 Oct 2026)

This backend branch also exposes a **trusted management GET only**
`/api/queue-retry-incidents?project=<canonical-id|all>` in `server.py`.
The handler applies the existing loopback/explicit management peer gate **before**
any SQLite access, accepts one exact project parameter, and does not touch
the writable portfolio queue pathways. It uses
`scripts/zcloud_queue_retry_live.py` to open existing `history.db` using
SQLite `mode=ro` + `PRAGMA query_only=ON`, then binds project grouping and
a top-40 incident sample in one deferred read transaction.

Response schema matches the independent zSentinel offline console
`zsentinel-retry-incident-v1` used by the Activity UI integration
[#1550](https://github.com/Zennay/zCloud/pull/1550):
- canonical P0–P3 project/status grouping and high-claim-attempt counts;
- total active items, ten-plus claim cycles, expired active lease timestamps;
- privacy-safe SHA256-derived 16-hex queue references, **never raw queue IDs,
  task titles, prompts, worker identities, claim tokens, evidence or metadata**;
- explicit bounded sample completeness; 40 rows only; hard-coded retry
  threshold 3 and no arbitrary SQL, path, or remote API parameters;
- explicit `database_read_only=true`, `safe_to_authorize_retry=false` and
  `attempts_count_claims_not_failures=true`.

Missing/unknown SQLite schema, unsupported project/priority, or a failed
read returns an unavailable response rather than an empty successful list.
Only a deliberate UI click invokes this endpoint; there is no scheduler or
background provider work. Two deliberate reads on the same scope can be
compared client-side without persisting raw queue data. Source-only until
[#1549](https://github.com/Zennay/zCloud/pull/1549), [#1550](https://github.com/Zennay/zCloud/pull/1550)
and the independent offline CLI [#1555](https://github.com/Zennay/zCloud/pull/1555)
are accepted under existing exact-head CI, independent review, protected-main
and serialized release policies. Native finite retry/backoff and automated
`NEEDS_AI` disposition from [#677](https://github.com/Zennay/zCloud/issues/677)
remain **not implemented**. No live queue, claims, workers, service or billing changed.

## 20 oktober · Activity: ook stille projecten bij korte pagina's zichtbaar

De bestaande read-only Activity-selector `scripts/zcloud_activity_fair_page.py`
is uitgebreid voor de situatie dat de opgevraagde paginagrootte **kleiner**
is dan het aantal echte projecten. Voorheen begon elke pagina opnieuw bij
het project met de nieuwste eerstvolgende gebeurtenis. Daardoor konden
drukke projecten meerdere pagina's lang terugkomen voordat een stiller
project zijn eerste rij te zien kreeg.

De selector geeft vanaf de tweede pagina **projecten met nog niet getoonde
gebeurtenissen eerst hun eerste zichtbare plaats**. Binnen die groep blijft
de originele UTC-tijdvolgorde gelden; zodra de eerder onzichtbare projecten
hun eerste plek kregen, blijven de normale round-robin-volgorde en de
bestaande per-project cursors intact. Er worden geen events weggeschreven,
gewist, gemarkeerd als verwerkt of verborgen. De scope blijft maximaal 32
projecten, 80 gebeurtenissen per pagina en bestaande SQLite read-only
keyset-query's.

De response bevat twee extra beschrijvende velden:
`coverage.projects_shown_in_page` en
`coverage.projects_not_yet_shown_with_events`. Een project dat nog
geen eerste plek heeft gehad is daarmee niet langer stilzwijgend gelijk
aan een project zonder gebeurtenissen. De eerdere
`github_pr_branch_ci=not_synchronized` en `feed_complete=false`
blijven ongewijzigd; de verbetering betreft **alleen de lokaal bekende
Activity-geschiedenis**. De GitHub Live-bron moet afzonderlijk worden
gecontroleerd voor echte merges, CI en actualiteit.

Code staat op bestaande [PR #1549](https://github.com/Zennay/zCloud/pull/1549).
Geen testsuites geschreven of handmatig uitgevoerd; geen productie-DB,
claim, worker, workflow of deployment gewijzigd. Exacte-head CI,
onafhankelijke review en release-autorisatie blijven vereist.

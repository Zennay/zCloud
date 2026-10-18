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

# zSentinel product-only queue admission (17 October user decision)

The portfolio queue's ordinary **new** worker admission now skips isolated QA, regression-test and audit-only work packages while still admitting actual product bugs, feature implementation, integrations, merges, and recovery tasks.

* Live eligibility, priority P0/P1, resource limits, manual-start preference, independent project lane/capability conflicts, reviewer gates and exclusive lease/claim rules remain unchanged.
* Already `claimed`, `running` or `verifying` work is **never cancelled, displaced or rewritten** by this feature. Its scope is included in the lane ownership graph even when a queued QA item is skipped.
* Metadata `lane`, `lane_id`, `focus`, `workstream` etc matching known QA-only lane IDs denies **new** admission; explicit QA/test/validation title prefixes also deny **unless the ticket requirements explicitly demand a real fix/build/delivery**. A P0 item called `Test urgent planner navigation` with a `Fix ...` completion criterion remains a P0 product fix; a pure expected-RED test carrier remains excluded.
* A legacy neutral-title task containing only a QA/regression-suite instruction with no actual delivery verbs is not freshly claimed. That task remains in the queue for existing owner-authorized re-scoping; no fake `done` or de-eligibility is written to SQLite.
* Lane selection sees only admissible queued candidates, so a high-priority old QA item does **not** hide the next buildable product item in the same lane. Active work is still visible for cross-lane scope protection.
* This is a **source change** to `server.py` + one pure policy module. No VPS, worker, DB or file data has been changed. It becomes operational **only after** the regular serialized production deployment and runner #1326 are actually restored and verified. All permanent existing CI and reviewer/approval gates still apply; this change adds no QA/test suite or synthetic fixture.

If the queue contains no eligible product item, leave workers idle rather than inventing a test round or taking over another worker's claim. Queue items remain auditable for manual owner review.

Admission scans at most four ordered 240-row queue pages (960 queued rows in total) to find up to 240 viable product candidates. This prevents a large stale QA backlog in the earliest priority page from hiding a runnable product ticket; the scan is read-only and bounded, with no scan beyond 960 rows automatically assumed complete.

The existing authorized `GET /api/portfolio-queue` now also exposes `delivery_policy` with queued QA-only held count, a bounded count and up to eight P0-first advisory product candidate IDs, explicitly flagged `not_claimed_or_authorized=true` and `admission_scope_lease_and_capacity_not_verified=true`. This is read-only, additive and does not modify the existing queue `items`, lease, route authorization or allocation response contracts.

Unparseable, duplicate-key, non-object or overlong workstream metadata now fails closed for **new** admissions, preventing an ambiguous serialized `qa-only` classification from being silently converted to product work. Existing active claims and historical queue records are never rewritten.

## Multilingual admission and truthful holds (20 October)

The source policy now recognises Dutch build/repair language (including `bouw`, `implementeer`, `herstel`, `repareer`, `integreer`, `oplossen`) alongside English product tasks. Dutch QA/validation lane labels, QA title prefixes, and criteria that request only a validation cycle are held **before a new worker can be admitted**. Explicit negations such as **do not merge** and **niet deploy** are not counted as positive delivery intent. A genuine delivery action embedded in a QA-prefixed legacy title is not blocked on title alone; an explicitly QA-only workstream remains excluded. No language heuristic grants claims or merge rights.

`product_delivery_disposition` has three outcomes: `product` (eligible for the *existing* subsequent authorization path), `qa_only` (hold new QA admission), and `invalid` (unknown/corrupt metadata or malformed ticket: fail closed). Existing `is_new_product_delivery` delegates to that same decision, avoiding a second classifier.

The read-only portfolio API's `delivery_policy` now separates `queued_qa_only_held` from `queued_invalid_or_unknown_held`; malformed metadata no longer increases a count labelled as QA when its malformed state reaches the source classifier. Both counts are limited to the returned visible queue items, not a complete queue-wide census. The queue API now pins a `delivery_policy_disposition` read-only annotation to **each original queued SQLite row before legacy JSON presentation normalization**. The dashboard's `invalid` count therefore sees the same corrupt/duplicate-key raw JSON refusal as new-worker admission, even though the historical `metadata` field remains normalized for compatibility. This annotation is advisory: it is not a lease, permission, merge authorization or permission to change an active worker. Do not infer full queue coverage from any visible API counters. Active claims and owner scopes are never released or displaced by either classification. All existing review, exact-HEAD CI, main-merge, resource and release gates remain mandatory; these committed source changes are **not** a live VPS deployment.

## Read-only worker-admission workbench (20 October, subsequent source commit)

The existing authenticated `GET /api/portfolio-queue` response now includes operator-facing queue pressure categories from the **visible response items only**:

* `queued_rows_visible`, `active_claimed_running_verifying_visible`, and `malformed_nonobject_rows` establish the input's observation scope.
* `queued_ineligible_held` and `queued_worker_slot_occupied` distinguish authorization/slot constraints from policy holds. They do **not** establish why a task was marked ineligible or who owns a slot.
* `queued_qa_only_held` and `queued_invalid_or_unknown_held` distinguish intentional no-QA policy from missing/corrupt classification evidence.
* `candidate_priority_counts` reports P0/P1/P2/P3/other **visible candidate** distribution; `candidate_list_truncated` warns that the up-to-eight visible candidate list hides additional matching rows.
* `source_coverage=visible_api_rows_only_not_full_queue` prevents those partial counts from being advertised as the full durable queue.

This product workbench is additive: the queue's existing `items` contract remains unchanged, and `not_claimed_or_authorized=true` / `admission_scope_lease_and_capacity_not_verified=true` remain unconditional. No auto-merge, queue write, worker admission, rate-limit change, CI dispatch or host mutation is implied. Merging to main and deploying are distinct owner-gated next steps.

## Source-consistent metadata receipt (20 October follow-up)

The queue's original `metadata_json` is classified once with the strict duplicate-key, bounded, nonfinite-rejecting reader **before** `_portfolio_queue_row` supplies a compatibility `metadata` object. A sanitized `{}` may still be rendered for legacy consumers, but `delivery_policy_disposition=invalid` remains visible on an originally invalid queued row and is consumed by the summary. Standalone calls to the pure summary function without a source receipt continue to classify their own items. No new live queue state is written, and no extra SQL read is required.

## Actionable hold reasons (20 October continuation)

Every queued row in the authenticated `GET /api/portfolio-queue` response now carries `delivery_policy_disposition` and a fixed `delivery_policy_reason`. Both are computed from the original SQLite row **before** legacy JSON presentation normalization. Invalid source metadata cannot silently masquerade as a valid product ticket in this interface.

The `delivery_policy` summary adds `queued_hold_reason_counts`, a bounded map of stable, non-sensitive reason codes. Examples: `explicit_qa_lane` (explicitly excluded from new dispatch), `qa_title_without_delivery` (legacy QA-only title without actual implementation), `invalid_metadata` (raw parse fails), `invalid_title` (ticket requires human correction), and `invalid_lane_metadata` (malformed ownership classification). Counters are observations over **only visible eligible, slot-free queued items**; they do not imply all repository tasks were scanned.

This is a **read-only operator explanation**, not a workflow or worker action. An invalid ticket must be re-scoped by its authorized owner; do not silently drop, unclaim, rewrite or relabel it. `product_candidate` is an input to existing downstream lane/lease/capacity gates, **not** permission to dispatch, merge or deploy. Active `claimed/running/verifying` tasks remain unchanged.

## Cross-project product leads, without extra worker claims

A global P0 queue can be dominated by one project even while other projects have independent, eligible product tickets. The read-only `delivery_policy` response now includes `project_shortlist`: the highest-priority **visible product candidate per project**, each with its observed candidate count and first queue identifier. The bounded list is sorted P0 first and then by candidate pressure; `distinct_projects_with_visible_product_work` and `project_shortlist_truncated` make partial display explicit.

**Do not confuse this with assignment.** `project_shortlist_is_advisory_only=true` is unconditional. A listed project must still pass its current Notion claim, file-scope ownership, project cap, lane overlap, worker capacity and exact runtime authorization. The existing global `candidates` list and actual allocator priority are **unchanged**; no round-robin dispatch, forced preemption, queue retry, claim or service action has been introduced. This list merely prevents a single chatty project's items from hiding independent work in the human operator's view.

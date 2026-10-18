# zSentinel Activity cockpit — operator guide (18 October 2026)

This is a product-first, explicit-action operator view on the existing **Activity** route. All new features are **read-only** and do not mutate portfolio-queue records, worker leases, priorities, runtime settings or deployments.

## Product delivery queue

Choose **Read admission** to request the existing authenticated `GET /api/portfolio-queue` endpoint. The new `delivery_policy` payload comes from unified backend PR #1549 and reports:

- `queued_product_candidates`: queued, eligible product work that passed the initial type classifier (not an authorized claim).
- `queued_qa_only_held`: queued-only QA/review packages skipped for fresh admission, not cancelled or marked Done.
- Up to eight candidate IDs, project IDs and priorities, with explicit **Not claimed** indicators.

The panel is intentionally not loaded in the background. An unavailable API or 403 produces an error instead of a false empty queue. A selected project narrows the visible candidates, while summary counts still describe the global queue; read the label before interpreting totals.

The type classifier never replaces existing scope/owner arbitration, hard resource caps, human approval, memory admission, or GitHub CI gates. `claimed`, `running`, `verifying` continue to reserve their respective ownership scopes. A queue receipt does not prove that an independent runner can execute the work.

## zSentinel issue-first delivery board

The **zSentinel · product delivery board** is displayed first on Activity, above the existing independent GitHub, issue and worker panels. Choose one canonical project and press **Read product sources** to explicitly read its public GitHub P0/P1 issue sample, public PR/CI/main observations and the authenticated queue admission preview. Each source is bounded and independent; if one fails, the other cards remain useful and the failing source is not interpreted as empty.

The board sorts observed open P0 issues ahead of P1 issues, shows relevant existing **open PR mentions** (as mentions or closing text only, not ownership), and separately lists provisional product queue candidates. No claim is inferred by matching GitHub issue IDs with queued project names. Open each original GitHub issue/PR through canonical links to verify context and current status before making a decision.

Manual readings for up to **eight** canonical projects coexist in the browser tab, with at most 150 issue rows and 200 GitHub events across projects. Reading a project again replaces only its own previous sample; snapshots are not cached on disk, sent to third parties other than the explicitly requested GitHub GET, or silently refreshed. The global counts in the queue panel remain global even with a specific project filter.

A missing source, a known partial window, an older observation, a linked draft or green CI always remains unverified. Source observations never authorize taking over a claimed lane or bypassing CI, review, release, human or zSSH capability gates.

### Export a privacy-minimized handoff

After manually reading relevant sources, choose **Export handoff .md** in the zSentinel board. This creates a local UTF-8 Markdown file with up to 60 high-priority GitHub issue references, up to three existing PR references per issue, at most 30 bounded public GitHub observations, and a high-level product queue summary. The export **does not include** queue item IDs, human claim IDs, event/prompt text, tokens, credentials, cookies or raw API payloads. All source timestamps and the evidence-only status are included.

The export action is local to the browser and requires a deliberate click; it never uploads, opens a new worker, contacts GitHub, or modifies runtime state. It is suitable as a *handoff note*, not an authority receipt. The next worker must still read current Notion ownership, exact PR checks and release gates before editing or merging.

## Product queue: QA-only versus needs-owner-review

The admission read-model differentiates **QA-only queued work held from fresh claims** from **malformed/ambiguous product work requiring an owner to correct the original queue entry**. In Activity, **Read admission** shows separate global counters. The **Product delivery board** puts bounded review-needed project/priority/reason entries above provisional product candidates. These rows are not automatically unlocked, claimed, dropped or classified as a completed fix.

The locally downloaded handoff contains no raw queue identifiers for these rows; it includes only their project, priority, constant reason (`invalid_metadata`, `invalid_title`, `oversized_criteria`) and total count. A real owner must correct source queue metadata through the existing authenticated control-plane path, after independent claim/lease/source review. This UI cannot grant itself permission to perform that correction. QA-only work remains excluded from *new* admission under the 17 October policy.

## Older local history

Choose **Read history** to request the first 40 items from `GET /api/activity-page`. Use **Load older** to follow the source-provided per-project keyset cursor; the list is capped at 400 distinct events in the browser. Switching Activity's project filter begins a separate scoped history. Results are not silently loaded, written to storage, or used to dispatch work.

The API reads canonical SQLite-local event identifiers/timestamps/kinds through a consistent read transaction and fairly interleaves project streams to prevent a chatty project from hiding quieter work. Only names/metadata necessary for local history are shown. A missing backend, invalid coverage, unexpected project, or failed request produces a visible error, not proof of zero activity.

**Coverage is partial by design.** The local feed does not synchronize GitHub PR, branch, merge or CI activity. The separate public GitHub button is an independent manual source. Never equate a branch update or an open PR with merged production delivery.

## Composer failure: actionable worker progress

When the separate actual v2 browser worker [PR #1552](https://github.com/Zennay/zCloud/pull/1552) reports a persistent missing composer, the backend read-only projection [PR #1549](https://github.com/Zennay/zCloud/pull/1549) retains only fixed, privacy-safe literal reason codes. Open Activity, select the affected project and worker slot, then choose **Read progress**. The panel shows the latest bounded browser-observed composer problem or restoration and explicitly says when **manual intervention in the existing assigned tab** is required. No underlying URL, free-form reason, prompt or token is exposed. A prior `composer-restored` observation is not proof the worker has an active queue claim or is generating.

There is **no reset button** here: only the owned v2 tab itself may request the pre-first-send, blank-chat, one-shot SPA repair. Do not use this UI to force navigation, dismiss a current reply or steal an existing assignment. The three PRs (#1549, #1550 and #1552) remain separate review/release dependencies.

## Directly from the VPS: on-demand GitHub and P0/P1 source buttons

In Activity, select exactly one project. **Read VPS GitHub** requests the new trusted `GET /api/github-activity-live?project=<id>` route. It returns an independently bound, read-only canonical repository observation (PRs, CI runs and main commits), with a maximum five-minute in-process cache. **Read VPS issues** requests `GET /api/product-issues-live?project=<id>`, which uses the original bounded, P0/P1-first issue source and existing open PR references. Both buttons show partial coverage and may report the separate `refreshing`, `unavailable` or `capacity_wait` outcomes. Operators can retry manually; no scheduled browser poll or automatic workflow dispatch is added.

The backend accepts only projects from canonical `projects.json`, and requires the existing trusted management network for these read-only API sources. A remote browser without that permission receives 403; this is **not** a cue to relax authentication. **Read public GitHub** and manual JSON imports remain usable separate evidence routes, while private repositories continue to require separately authorized access and are never silently marked empty.

A matching GitHub event ID is not a local SQLite event, a CI success is not a merge, a reported GitHub merge target is not proof a commit reached main, and a main commit does not prove the production VPS has deployed it. All receipts explicitly carry `feed_complete=false` and deny worker recovery/merge/deploy authority. Two concurrent provider collections maximum; source reads are bounded and only started by a button click. Check actual Notion claims and immutable CI/review gates before implementation or release.

## Integration and release

- Backend: [zCloud #1549](https://github.com/Zennay/zCloud/pull/1549) — read-only activity paging and product-only queue admission.
- UI: [zCloud #1550](https://github.com/Zennay/zCloud/pull/1550) — optional panels, explicit loading/errors and pinned JS source.
- Source owner: [#1536](https://github.com/Zennay/zCloud/pull/1536) — original product-only classifier reused, not independently merged.
- Incident: [#1326](https://github.com/Zennay/zCloud/issues/1326) — self-hosted zCloud listener needs independent authorized repair.

These PRs are **not production** until exact-head required GitHub checks succeed, independent review is complete, protected-main merge is permitted and the serialized VPS release is independently verified. Do not enable GitHub-hosted billing, bypass a reviewer, restart an unauthorized service or displace a worker to demonstrate the preview.

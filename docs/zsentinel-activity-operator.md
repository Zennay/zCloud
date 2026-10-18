# zSentinel Activity cockpit — operator guide (18 October 2026)

This is a product-first, explicit-action operator view on the existing **Activity** route. All new features are **read-only** and do not mutate portfolio-queue records, worker leases, priorities, runtime settings or deployments.

## Product delivery queue

Choose **Read admission** to request the existing authenticated `GET /api/portfolio-queue` endpoint. The new `delivery_policy` payload comes from unified backend PR #1549 and reports:

- `queued_product_candidates`: queued, eligible product work that passed the initial type classifier (not an authorized claim).
- `queued_qa_only_held`: queued-only QA/review packages skipped for fresh admission, not cancelled or marked Done.
- Up to eight candidate IDs, project IDs and priorities, with explicit **Not claimed** indicators.

The panel is intentionally not loaded in the background. An unavailable API or 403 produces an error instead of a false empty queue. A selected project narrows the visible candidates, while summary counts still describe the global queue; read the label before interpreting totals.

The type classifier never replaces existing scope/owner arbitration, hard resource caps, human approval, memory admission, or GitHub CI gates. `claimed`, `running`, `verifying` continue to reserve their respective ownership scopes. A queue receipt does not prove that an independent runner can execute the work.

## Older local history

Choose **Read history** to request the first 40 items from `GET /api/activity-page`. Use **Load older** to follow the source-provided per-project keyset cursor; the list is capped at 400 distinct events in the browser. Switching Activity's project filter begins a separate scoped history. Results are not silently loaded, written to storage, or used to dispatch work.

The API reads canonical SQLite-local event identifiers/timestamps/kinds through a consistent read transaction and fairly interleaves project streams to prevent a chatty project from hiding quieter work. Only names/metadata necessary for local history are shown. A missing backend, invalid coverage, unexpected project, or failed request produces a visible error, not proof of zero activity.

**Coverage is partial by design.** The local feed does not synchronize GitHub PR, branch, merge or CI activity. The separate public GitHub button is an independent manual source. Never equate a branch update or an open PR with merged production delivery.

## Integration and release

- Backend: [zCloud #1549](https://github.com/Zennay/zCloud/pull/1549) — read-only activity paging and product-only queue admission.
- UI: [zCloud #1550](https://github.com/Zennay/zCloud/pull/1550) — optional panels, explicit loading/errors and pinned JS source.
- Source owner: [#1536](https://github.com/Zennay/zCloud/pull/1536) — original product-only classifier reused, not independently merged.
- Incident: [#1326](https://github.com/Zennay/zCloud/issues/1326) — self-hosted zCloud listener needs independent authorized repair.

These PRs are **not production** until exact-head required GitHub checks succeed, independent review is complete, protected-main merge is permitted and the serialized VPS release is independently verified. Do not enable GitHub-hosted billing, bypass a reviewer, restart an unauthorized service or displace a worker to demonstrate the preview.

# GitHub activity source for zCloud (#1518)

This is a **real, opt-in read-only source adapter**, not a claim that the live Activity page is synchronized with GitHub. The existing dashboard endpoint reads only local VPS events. A connected `/api/status` is not evidence that remote PRs, branches or CI are covered.

## One-command private operator experience

An operator can now create an end-to-end private Activity dashboard with **one explicitly initiated command** (fetch GitHub → fair bounded local history → readable responsive HTML). This still does not start a service, schedule background work or change the production dashboard:

```sh
python3 -m scripts.zcloud_activity_refresh_report \
  --projects cloud,supa \
  --ledger ./private/github-activity-ledger.json \
  --html ./private/github-activity-report.html

# When GitHub is unavailable, render only previously retained 0600 data:
python3 -m scripts.zcloud_activity_refresh_report \
  --offline --ledger ./private/github-activity-ledger.json \
  --html ./private/github-activity-report.html
```

The offline command never calls GitHub. A partial/offline fetch never erases retained historical events. Both outputs must be distinct private paths outside `public/`; a symlink resolving into the public web root is refused. The repository now includes a dedicated `private/.gitignore` so local operator-owned ledger and HTML files placed in `./private/` cannot accidentally enter normal Git staging. Only the operator's explicitly selected projects appear in the report, even if their private ledger contains other projects. Source lag and partial coverage are visible; the output explicitly says it **did not update the live zCloud Activity page**.

## Source contract

`scripts/zcloud_github_activity.py` reads canonical `projects.json` entries with `https://github.com/Zennay/<repo>` and calls only GitHub's REST **GET** endpoints. It has no access to production SQLite, queue leases, worker dispatch, automation control or deploy APIs.

```sh
python3 -m scripts.zcloud_github_activity --projects cloud,supa,mixlab
# Keep a bounded read-only-source history across transient failures:
python3 -m scripts.zcloud_github_activity --projects cloud,supa --ledger ./private/github-activity-ledger.json
# Optional restricted local receipt, kept OUT of Git and OUT of public/:
python3 -m scripts.zcloud_github_activity --projects cloud,supa --output /tmp/zcloud-github-activity-private.json
```

Set `GITHUB_TOKEN` or `GH_READ_TOKEN` only in an approved private runtime environment if private repository metadata must be read. A token needs read-only repository metadata/actions permissions; it is neither printed nor included in the output. Do not commit the resulting receipt.

The optional `--ledger` is a separate, explicitly operator-enabled private retained receipt. It keeps up to 1,600 stable-ID events in a size-limited regular file, rejects symlink/unsafe permissions, and retains earlier observations during offline, partial or rate-limited reads. It never treats retained history as current sync. Nonselected projects stay out of the returned view even if the same private ledger previously held their records. Run only one writer per ledger file; concurrent invocations are not yet a supported contract.

The adapter emits `schema_version=1`, `coverage` keyed by project, `events`, `events_truncated` and `feed_complete=false`. Four source families are kept separate: `pr_created`, `pr_updated`/**`pr_merged`**, `main_commit`, `ci_run`, and `branch_tip_observed`. A closed PR is **not** a merge without a valid `merged_at`, and a successful CI run is never interpreted as main integration. CI observations include **all repository branches** (including PR heads), keyed by run ID, attempt and head SHA; they are not incorrectly limited to runs from `main`. Branch-list responses do not include timestamps; they get `source_timestamp=null` and **first observed** time only. Branch labels are hashed, not published.

Coverage records per-family `recent_window`, `truncated_after_two_pages`, a generic read failure or request-budget exhaustion. A partial or private/not-authorized source does **not** become a fabricated empty history or a successful global feed. Source timestamp (GitHub) and fetch observation timestamp remain distinct. `last_successful_source_fetch` advances after an actually successful bounded source fetch, even with incomplete paging; `last_successful_sync` advances only when all four source families have a fully fetched recent window. An old full-sync timestamp is preserved as stale historical evidence rather than replaced by a partial fetch.

## Bounds and privacy

- At most 12 explicitly selected projects and 24 API requests globally; 30 items/page, at most two pages/source; eight-second request timeout; 2 MiB/response.
- API host fixed at `api.github.com`; redirects rejected. Only canonical repository URLs from the checked-in registry are admitted. Duplicate-key JSON and malformed receipts fail closed per source.
- At most 480 distinct events per receipt, **round-robin across selected projects**, so a noisy project cannot entirely hide quiet projects. IDs include repository, source kind, GitHub number/SHA/attempt/time.
- No PR descriptions, commit messages, issue titles, personal identities, raw branch names, credentials or runner command arguments leave the parser. The optional receipt is written atomically with permissions 0600.
- All values are public/source metadata even when querying a private repository. Keep output access controlled; no automatic publication.

## Required integration (deliberately NOT bypassed)

The existing [fair local event selector PR #1519](https://github.com/Zennay/zCloud/pull/1519) owns event paging; [Activity UI PR #1528](https://github.com/Zennay/zCloud/pull/1528) owns `public/app.js`; central eventstream and `server.py` are serialized integration surfaces. They are left unchanged here. An authorized owner can consume this adapter's typed, bounded receipt in a separate queue-safe ingest job, persist source timestamps and first-observed timestamps and expose actual source coverage alongside the local feed. **Do not merge remote and local events solely by display text**. Preserve project identity, stable id, source kind and explicit main-merged semantics.

A second product interface, `scripts/zcloud_activity_source_page.py`, exposes read-only **per-project, 1–80-item pages** from the explicitly private ledger (no network call or service mutation). The cursor binds to the exact ordered source snapshot **including event status and coverage**, so after a refresh the reader gets an explicit `activity_cursor_stale_restart` rather than duplicate/skipped activity or a fabricated consistent continuation:

```sh
python3 -m scripts.zcloud_activity_source_page --ledger ./private/github-activity-ledger.json --project cloud --limit 40
# Pass the returned next_cursor in a later --cursor argument only if ledger has not changed.
```

The returned event data is always described as `source_status=partial_or_retained`, with its real source timestamps and previous sync coverage; it never marks a cached read as live GitHub synchronization. Both command modules are optional source tools, outside production routing.

The optional **standalone operator report** is now a usable offline HTML dashboard (no JavaScript, remote assets, HTTP server or live data calls). It shows per-project PR merges **to main**, head-SHA commits and CI outcomes as separate measures, with expandable source coverage, last partial-fetch time and last full recent-window sync. The visible 500-event maximum is distributed fairly rather than letting one noisy project dominate.

```sh
python3 -m scripts.zcloud_activity_operator_report \
  --ledger ./private/github-activity-ledger.json \
  --output ./private/github-activity-report.html
# Open the generated file locally as an operator. It has restrictive 0600 mode.
```

The ledger, JSON export and HTML report explicitly reject destinations inside the checkout's served `public/` tree (including a symlinked parent path) to avoid accidental disclosure of private repository metadata. Treat other hosting/static locations as sensitive too; operator-specified output must remain private.

The ledger is optional and no persistent file is opened during the default read-only CLI mode. No runtime ingestion, dashboard switch, live VPS roll-out, worker recovery or release is claimed. Mandatory exact-head CI, ownership review and serialized production #580/PWQ-41/#1089 remain required before wiring/deploy. This implementation contains no new QA or regression suite per the 17 October user decision.

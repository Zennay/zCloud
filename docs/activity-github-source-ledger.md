# GitHub activity source for zCloud (#1518)

This is a **real, opt-in read-only source adapter**, not a claim that the live Activity page is synchronized with GitHub. The existing dashboard endpoint reads only local VPS events. A connected `/api/status` is not evidence that remote PRs, branches or CI are covered.

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

The adapter emits `schema_version=1`, `coverage` keyed by project, `events`, `events_truncated` and `feed_complete=false`. Four source families are kept separate: `pr_created`, `pr_updated`/**`pr_merged`**, `main_commit`, `ci_run`, and `branch_tip_observed`. A closed PR is **not** a merge without a valid `merged_at`, and a successful CI run is never interpreted as main integration. Branch-list responses do not include timestamps; they get `source_timestamp=null` and **first observed** time only. Branch labels are hashed, not published.

Coverage records per-family `recent_window`, `truncated_after_two_pages`, a generic read failure or request-budget exhaustion. A partial or private/not-authorized source does **not** become a fabricated empty history or a successful global feed. Source timestamp (GitHub) and fetch observation timestamp remain distinct.

## Bounds and privacy

- At most 12 explicitly selected projects and 24 API requests globally; 30 items/page, at most two pages/source; eight-second request timeout; 2 MiB/response.
- API host fixed at `api.github.com`; redirects rejected. Only canonical repository URLs from the checked-in registry are admitted. Duplicate-key JSON and malformed receipts fail closed per source.
- At most 480 distinct events per receipt, **round-robin across selected projects**, so a noisy project cannot entirely hide quiet projects. IDs include repository, source kind, GitHub number/SHA/attempt/time.
- No PR descriptions, commit messages, issue titles, personal identities, raw branch names, credentials or runner command arguments leave the parser. The optional receipt is written atomically with permissions 0600.
- All values are public/source metadata even when querying a private repository. Keep output access controlled; no automatic publication.

## Required integration (deliberately NOT bypassed)

The existing [fair local event selector PR #1519](https://github.com/Zennay/zCloud/pull/1519) owns event paging; [Activity UI PR #1528](https://github.com/Zennay/zCloud/pull/1528) owns `public/app.js`; central eventstream and `server.py` are serialized integration surfaces. They are left unchanged here. An authorized owner can consume this adapter's typed, bounded receipt in a separate queue-safe ingest job, persist source timestamps and first-observed timestamps and expose actual source coverage alongside the local feed. **Do not merge remote and local events solely by display text**. Preserve project identity, stable id, source kind and explicit main-merged semantics.

The ledger is optional and no persistent file is opened during the default read-only CLI mode. No runtime ingestion, dashboard switch, live VPS roll-out, worker recovery or release is claimed. Mandatory exact-head CI, ownership review and serialized production #580/PWQ-41/#1089 remain required before wiring/deploy. This implementation contains no new QA or regression suite per the 17 October user decision.

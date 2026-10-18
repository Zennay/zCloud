# Combined local VPS + GitHub Activity (operator-only)

This optional **offline product view** composes the existing retained GitHub source ledger with one already-exported `GET /api/activity` JSON response. By default it reads local files only; optional operator flags make read-only GET requests to the fixed loopback API and canonical GitHub repositories. It never calls workers, queues, the production database, or any write/mutation endpoint. It reads no secrets, titles, messages or event details into the HTML. It is not the live zCloud Activity screen.

```sh
# Use an existing operator-authorized, privately stored JSON array from
# GET /api/activity or GET /api/activity?project=cloud (max 80 rows).
# Store it outside the public/ tree; do not commit private snapshots.
python3 -m scripts.zcloud_activity_combined_report \
  --ledger ./private/github-activity-ledger.json \
  --local-json ./private/local-activity.json \
  --output ./private/combined-activity.html
```

Optionally add `--project cloud` to view one known project. Both tables are deliberately separate. GitHub PR merges to `main` are indicated only by the original typed GitHub source; a local VPS milestone or CI-success record does **not** become a verified release. Local events have their recorded UTC time but are **not** evidence the local API remains live.

**Privacy and bounds:** The private ledger is loaded by the existing strict reader. Local input must be a bounded regular file (<=1 MiB), no symlink, <=80 records, JSON array with unique field keys and known projects. Only the local `project` and `ts` fields appear in the output. Raw local `id`, `title`, `detail` and unknown fields are never echoed. HTML uses the existing 0600 atomic private writer that refuses paths inside `public/` (including symlink ancestry). No QA/test suites or production control-plane files are changed.

**Remaining production task:** Authorized eventstream owner must integrate the GitHub collector into the live event API with separate timestamps, fair paging, error/lag indicators and explicit release approval; the existing local dashboard only uses local project history. zCloud #1326 runner/service and #580/#1089 serial release holds remain intact.


## Direct local API (no manual JSON export)

Run from an operator-authorized machine where the zCloud local HTTP listener is genuinely on loopback port 8765:

```sh
python3 -m scripts.zcloud_activity_combined_report \
  --ledger ./private/github-activity-ledger.json \
  --local-api --output ./private/combined-activity.html
```

This uses only **GET** on literal `http://127.0.0.1:8765/api/activity?project=<allowlisted-id>`, never external addresses, proxies, redirected hosts, credentials or POST. At most 12 known projects, three-second timeout per source and one MiB response cap; each source has its own 80-record API window. Unavailable projects appear as **source unavailable**, not as having zero events. A successful request only attests to that one local read, **not** ongoing service availability, a merged GitHub PR or any live worker recovery. `--local-api` and `--local-json` are mutually exclusive.


## One-command combined source refresh

From an authorized operator environment, explicitly fetch bounded, read-only GitHub metadata for canonical project IDs and read local VPS event windows before rendering one private HTML report:

```sh
python3 -m scripts.zcloud_activity_combined_report \
  --refresh-github --projects cloud,supa,mixlab \
  --ledger ./private/github-activity-ledger.json \
  --local-api --output ./private/combined-activity.html
```

The command performs GET-only GitHub requests via the existing `GithubReader` (strict repository allowlist, no redirects, per-project endpoint budget, bounded pagination), updates only the **operator-owned private 0600 ledger**, then reads at most 12 local project event windows through fixed `127.0.0.1:8765` GET. It produces a private 0600 HTML file. The report includes **only** explicitly requested projects even if previous ledger history contains others. Private repositories require an optional read-only token through `GITHUB_TOKEN` / `GH_READ_TOKEN` in the operator runtime; never paste the token into the command or report.

If remote GitHub is unavailable, omit `--refresh-github --projects` to render retained history; GitHub source status remains partial/stale, never fabricated current. The command rejects missing, duplicate, oversized or noncanonical project scopes, and disallows output, ledger and local-JSON input aliases (including symlink-resolved paths), so an HTML report cannot overwrite its own source evidence. This is still **not** a production dashboard update, release permission, working worker, or proof of a merged feature. Existing exact-head CI and serialized production ownership are unchanged.

## Actuele 1/6/12-uurproducttijdlijn

Het offline gecombineerde HTML-rapport bevat nu per project en venster aparte brongebaseerde tellingen van **PR-merges naar main**, commits op main, nieuwe PR's, CI-succes/fouten en lokale VPS-events. De tijdvensters worden gerekend vanaf het zichtbare UTC-rapportmoment, uitsluitend op basis van echte bron-tijdstippen; `branch_tip_observed` zonder GitHub-brontijd telt niet als recent werk. CI en PR-merges worden nooit opgeteld tot vermeende geleverde features. Een zichtbare nul bewijst niet dat er geen activiteit was: de lokale API heeft een maximaal 80-eventvenster en de GitHub-bronnen zijn eveneens begrensd of gedeeltelijk. Het rapport bewijst geen deploy of hervatte worker. De operator start dit bewust, zonder wijzigingen aan live systemen.

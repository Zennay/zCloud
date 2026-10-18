# Combined local VPS + GitHub Activity (operator-only)

This optional **offline product view** composes the existing retained GitHub source ledger with one already-exported `GET /api/activity` JSON response. It never contacts GitHub, the VPS, workers, queues, the production database, or external providers. It reads no secrets, titles, messages or event details into the HTML. It is not the live zCloud Activity screen.

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

# zCloud

A dependency-free, read-only project dashboard deployed at `/home/ubuntu/zennay-cloud` on the existing VPS. Run `python3 server.py`; default port 8765. Environment overrides: `ZENNAY_BIND`, `ZENNAY_PORT`.

## Data and progress

- CPU, memory, disk and uptime: Linux `/proc` and `shutil.disk_usage`; every 15 seconds.
- Project Git and systemd state: every 60 seconds. The existing self-hosted runner checkouts are shallow; commit counts are checkout counts, not lifetime project totals. No fetch, checkout, service control or trainer commands are performed.
- HaxLab replay statistics: read-only SQLite connection, one-second query budget.
- FTMO's successful inactive oneshot task is shown as waiting while its timer is monitored separately.
- Progress is the fraction of completed milestones in `projects.json`, rounded to whole percentages. HaxLab and FTMO plans were imported from the previous dashboard, not independently validated. These percentages do not measure AI performance or trading profitability.
- To change milestones, edit only `projects.json`, set the appropriate `done` flags and increment `milestone_revision`. The collector reloads it every minute. The next sample records the updated percentage; a milestone event records its first observation.
- Project and host history is stored automatically every five minutes in `history.db`, even without an open browser. Original `snapshots` rows are imported without deletion. Legacy timestamps use the original VPS UTC timezone. UI dates use Europe/Amsterdam.
- Host history retention: 366 days; project history and deduplicated events retained. The UI shows up to 80 recent activity events and a 24-hour host chart. Shallow Git history starts being accumulated when the monitor sees a commit; it is not a complete reconstruction of prior development.

## Routes

`/` overview, `/#project/haxlab`, `/#project/ftmo`, `/#project/cloud`, `/#activity`, `/#infrastructure`.

Read-only JSON: `/api/status` (alias `/api/v1/status`), `/api/history?project=haxlab&range=7d` (24h/7d/30d/all), `/api/activity?project=haxlab`, `/api/host-history`, `/api/v1/watch`.

Watch API v1 returns a compact timestamp, stale flag, CPU/memory and project summaries. **zCloud Wear** is the native Wear OS component of zCloud; its debug APK builds successfully, with physical Watch validation still open. On the existing HTTP origin the dashboard is a responsive website with a web manifest, not an offline-capable or secure-context PWA. Existing network exposure is retained; no authentication, TLS, firewall or DNS changes are included.

`/api/snapshot` is retained only for loopback compatibility. All static files are explicitly allowlisted. Database, configuration, server source and backups cannot be served. Every request carries no-store, nosniff and a restrictive CSP; all Git strings are HTML-escaped by the frontend.

## Deployment and rollback

Only `zennay-cloud.service` is restarted. The obsolete `zennay-cloud-snapshot.timer` is disabled because the server now owns sampling. No HaxLab/FTMO runner, application or Firefox service is changed.

Release backup directories on the VPS contain original `server.py`, `index.html`, and a SQLite-consistent database copy. To roll back, stop only zennay-cloud, restore the original Python/HTML and consistent database backup, then start zennay-cloud and re-enable the original snapshot timer. Keep the new database separately to preserve any newly collected history.

Validation: staged real VPS metrics, JSON endpoints, database migration and static-file allowlist checks. DOM interaction tests cover project navigation, milestone counts, chart points/ranges, activity filters, the infrastructure view, offline recovery, and unknown projects. Full visual/browser QA was blocked: the cloud browser forces HTTPS on this existing HTTP-only endpoint; its localhost preview is blocked as well. Responsive CSS is implemented, but actual mobile/desktop layout has not been visually verified in this session. The screen does not fabricate historical progress or sample metric values.
## Last-known-good recovery

zCloud keeps recovery snapshots outside the repository at `~/.local/state/zcloud/recovery`. Runtime state and secrets such as `history.db`, ChatGPT project/conversation mappings, watch tokens, management allowlists and TLS keys are **never** restored by this mechanism.

Before marking a release as safe, run the regression smoke-suite and live health check. Then capture the healthy tree with evidence:

```bash
python3 scripts/zcloud_recovery.py capture --evidence "10/10 regression tests green; live API 200"
python3 scripts/zcloud_recovery.py status
```

Rollback is one action:

```bash
python3 scripts/zcloud_recovery.py rollback
```

Rollback verifies the stored snapshot hashes first, backs up the current managed source/config tree, stops only `zennay-cloud.service`, restores the last-known-good tree, starts the service to the recorded state, checks `/api/status`, and verifies that the project/chat mapping fingerprint in `history.db` did not change. If restore or health validation fails, it automatically restores the pre-rollback files and records the failure in `recovery.log`.
## Durable worker/task claims

zCloud keeps active task/capability ownership in SQLite so parallel workers cannot silently take the same work item.

- POST /api/task-claims with action=acquire, project_id, claim_key, owner_id, optional worker_id and lease_seconds.
- action=heartbeat renews only the current, unexpired owner.
- action=release releases only the current owner.
- GET /api/task-claims?project=<id> lists active claims and automatically removes expired leases.
- Claim acquisition is a single SQLite upsert guarded by the existing (project_id, claim_key) primary key, so concurrent contenders produce one owner and conflicts return HTTP 409.
- Lease duration is bounded to 15–3600 seconds. A stale/expired claim can be atomically recovered by the next worker.

The endpoint uses the same trusted-management boundary as other zCloud control actions. Do not put secrets in claim metadata.
# New VPS zCloud workers — staged cutover (22 October 2026)

Target host: `VM-7eb5ee16-2c71-440d-b7fd-8095d204e930`. The historical DB snapshot transferred to `/home/ubuntu/zcloud-history-migration.db` has **23 tables** and is readable, but **is not an authoritative final sync** if the old VPS kept working after its capture.

This route distinguishes three independent things often all called *workers*:

1. **Browser/ChatGPT project workers:** the zCloud runner state in SQLite, an already authenticated Firefox profile, the zCloud extension, `web-ext`, an accessible `ubuntu` X display, and a supervised Firefox session.
2. **zCloud API, queue and scheduler:** `server.py`, one authoritative `history.db`, and the `zennay-cloud.service` unit. A database with a worker desired state does **not** prove an executing browser.
3. **GitHub self-hosted Actions runners:** separate GitHub registrations and Linux services. Cloned registration credentials/identities must not be started on both machines. The current permanent-host workflows still contain `vps-bb300bba` identity guards; their migration must be reviewed separately.

## 1. Prepare on the NEW VPS (root)

From the checkout containing the installer:

```sh
cd /home/ubuntu/zennay-cloud
sudo bash ops/new-vps-worker-bootstrap-20261022.sh prepare
```

This installs missing minimal launch dependencies, stages the historical SQLite backup **only if `history.db` is absent**, stages systemd service definitions if absent, and enables `ubuntu` lingering. It does not stop or start a running worker or service, overwrite an existing database, or register a GitHub runner. Existing local or user-level services remain untouched.

```sh
sudo bash ops/new-vps-worker-bootstrap-20261022.sh status
```

Inspect missing browser components and the current API status. The reference Firefox service expects the `ubuntu` desktop on `DISPLAY=:10` and a pre-existing authenticated `snap/firefox/common/chatgpt-webext-profile`. A new KDE/XRDP user `desktop` is **not** automatically equivalent. If the profile or display is missing, do not force a browser start or create empty worker sessions. Restore the original profile securely to the correct user and confirm the actual desktop session first.

## 2. Safe handover

1. Verify where the old zCloud server is writing, and pause the **old** scheduler, worker tabs and write jobs after outstanding claims have been reconciled. Do not broadly kill Firefox or cancel unrelated GitHub/FTMO jobs.
2. Create a **new consistent SQLite backup** from the final old database state after the writer fence. Transfer it and reconcile any newer new-VPS rows. Neither `cp` of a live SQLite/WAL database nor blindly overwriting an existing `history.db` is safe.
3. Preserve both pre-cutover copies in immutable/dated backup locations and verify worker/project/queue tables, lease timestamps and `PRAGMA quick_check`.
4. Confirm the new-vs-old runner identity/label migration is designed before enabling any GitHub Actions listener. Old runners remain disabled on the new host unless explicitly re-registered with fresh repository-scoped credentials.

## 3. Activate the worker desired state only after step 2

```sh
cd /home/ubuntu/zennay-cloud
sudo ZCLOUD_OLD_VPS_FENCED=yes ZCLOUD_FINAL_DB_SYNCED=yes \
  bash ops/new-vps-worker-bootstrap-20261022.sh activate
```

The two flags are explicit cutover attestations, **not** synchronization tools. Never set them merely because a 657 MB file exists. The script refuses active local zCloud services, active cloned Actions runner identities, missing schema, missing targets, and unexpired queue claims. It updates the desired fleet using the existing host-bound `scripts/apply_worker_fleet.py`, then starts zCloud locally. It starts the Firefox worker supervisor only when the original browser prerequisites and X display are verified.

The checked-in `worker-fleet.json` currently requests **10 workers** across zSentinel (5), Tuddy (1), FTMO (1), FuckBall (1), TradeEngine (1), LightUp (1). This is *planned capacity*; `/api/runner-live`, actual recent heartbeats and project queue transitions must confirm which workers are executing. It is not an instruction to claim 10 logged-in sessions exist.

## 4. Independent acceptance

- `systemctl status zennay-cloud.service` and `systemctl --user -M ubuntu@ status chatgpt-firefox.service` are healthy.
- From the VPS, `curl -i http://127.0.0.1:8765/api/status` and `curl -i http://127.0.0.1:8765/api/runner-live` show expected status and live worker heartbeats. A 401, 404, a nonzero HTTP code, or empty/stale heartbeats is **not** evidence of running workers.
- Firefox must remain authenticated and persist tabs across disconnect/reboot without generating duplicate sessions; verify this in the appropriate XRDP login.
- Register new unique GitHub Actions runner identities with valid repo/group scope; then update trusted workflow host admission under its existing release-owner gates, and confirm a job has `runner_id > 0`. Do not reuse the old `zcloud-vps-1` registration.
- Only once the new system actually executes jobs, retire old server and runner services; keep backups.

Never publish registration tokens, browser sessions, private profile bytes or SQLite files in CI logs or GitHub.

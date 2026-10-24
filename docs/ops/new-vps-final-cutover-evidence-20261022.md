# New VPS final cutover admission (read-only)

This lane is **separate** from the SQLite transfer, worker activation, runner
registration and storage-mount owners. It supports [migration issue #1588](https://github.com/Zennay/zCloud/issues/1588).

Run on the **new** VPS as a non-privileged user after the other lanes have
actually finished, with an operator-assembled JSON manifest of source receipts:

```bash
python3 ops/new-vps-cutover-gate-20261022.py \
  --receipts /path/to/operator-protected-cutover-receipts.json \
  --training-mount /path/to/dedicated-training-disk
```

The command never mutates a service or database and **never** stops the old VPS.
Exit 2 / status HOLD is the expected result until all proof exists. Exit 0 /
READY_FOR_HUMAN_REVIEW only means the evidence format and freshness conditions
were met; it is **not approval or proof that self-reported receipts are true**.
A human still checks original GitHub job pages, actual SQLite backups, real
browser events, TLS/auth, and rollback availability.

## Receipt manifest contract

- `target_hostname`: exact new hostname
- `collected_at`: timezone-aware ISO-8601 timestamp, no older than 60 minutes
- `database`: `old_writers_fenced`, `new_writer_singleton`,
  `restored_on_new`, `wal_consistent` must be true;
  `final_backup_sha256` must be a real 64-character hex digest;
  `source_tables` and `target_tables` equal, positive integer;
  `snapshot_at` fresh within 24 hours
- `scheduler`: `old_disabled` and `new_active` true;
  `last_completed_dispatch_at` fresh within one hour
- `runners`: exactly 14 distinct entries with `runner_id` > 0,
  unique `name` and `job_id` > 0, `job_conclusion: success`,
  `repository`, `host` matching new VPS and recent `job_completed_at`
- `workers`: exactly 10 distinct slots numbered 1–10, unique `worker_id`,
  new-VPS `host`, `heartbeat_at` within five minutes and
  `last_event_at` within one hour
- `public_routes`: `name: zcloud_dashboard` and `name: zssh_plugin`,
  HTTPS `url`, `tls_verified`, `authentication_verified`,
  `new_vps_origin_verified` true, `checked_at` within one hour
- `project_services`: one record for HaxLab, FTMO, TradeEngine, zCloud,
  Supa, RaiseAI, uLab, zSSH, LightUp, zGuard/zBrowse, MixLab, Fuckball,
  Tuddy, BrowserAgent and zSentinel. Each has `state`
  `healthy` or `intentionally_offline`, a recent `checked_at`, and
  nonempty `evidence` identifying the functional proof or explicit reason.
- `rollback`: `old_host_preserved`, `restore_procedure_confirmed`,
  `backup_off_host` true.

All timestamps require a timezone; future timestamps beyond two minutes are
rejected. The manifest must be populated from source evidence — **not**
invented placeholders or a template marked true. Do not put tokens, database
contents, passwords, session cookies or private keys in the manifest.

## Training storage

This tool inspects the running host's `/proc/self/mountinfo` and
`/sys/dev/block`. It requires an exact canonical mountpoint with ext4/xfs/btrfs,
a local `/dev/` block source, and a *different physical parent disk* from
the root filesystem. Root partitions, bind-mounts, FUSE/rclone, network mounts,
tmpfs and missing/unreadable mount evidence are rejected. It does not create
a mount or start training.

## Production release boundary

The dashboard/API and job receipts, auth checks, restore proof and runner
assignments are **operator-attested** and must be manually independently
confirmed. This script's verdict must never be wired as the sole prerequisite
for stopping the old host or granting production deployment privileges.
Existing branch protections, CI, admin approvals and rollback requirements
remain in force. No QA or regression suite is added or run by this lane.

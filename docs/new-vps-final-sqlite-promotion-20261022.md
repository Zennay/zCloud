# Final zCloud SQLite promotion — new VPS only

This is the final DB-promotion component between zCloud service PR #1583 and browserworker PR #1578. Neither production services nor old-host data are modified by committing this code.

New host: VM-7eb5ee16-2c71-440d-b7fd-8095d204e930.
Old host: vps-bb300bba. Preserve old VPS as rollback.

## Old-host owner: prerequisites

1. Under the existing release owner/lock, quiesce ALL old API/SQLite/scheduler/browser writers and ensure no active queue claims. Never stop unrelated GitHub runners.
2. With old writers fenced, create a consistent SQLite online backup which incorporates WAL changes; transfer the resulting standalone DB through the authorized secure channel to /home/ubuntu/zcloud-history-final.db on the new VPS.
3. Chown the transferred file to ubuntu and chmod 0600. Confirm it is regular, not a symlink, and has no pending -wal/-shm/-journal dependency.
4. Supply a separate private ubuntu-owned JSON receipt at /home/ubuntu/zcloud-old-writer-fence.json with these fields, populated from actual verified evidence (the timestamp shown is only a placeholder):

~~~json
{
  "old_host": "vps-bb300bba",
  "new_host": "VM-7eb5ee16-2c71-440d-b7fd-8095d204e930",
  "old_writer_fenced": true,
  "old_scheduler_inactive": true,
  "old_browser_workers_inactive": true,
  "sqlite_backup_complete": true,
  "pending_wal_checkpointed": true,
  "fenced_at_utc": "REPLACE_WITH_CURRENT_UTC_ISO_TIMESTAMP",
  "db_sha256": "REPLACE_WITH_SHA256_OF_FINAL_TRANSFERRED_DATABASE"
}
~~~

This receipt is human/operator attestation, not cryptographic proof of the remote process state. The script requires exact host identity, exact source hash and a timezone-aware fence timestamp no older than four hours. Never attest an unverified stop.

## New-host owner: opt-in execution

Requires approved root execution on the exact NEW host and integration through the existing code-review, CI and release gates. The current zSSH 3.0 private connector does not have these execution/systemd rights; do not expand permissions implicitly.

1. Confirm no untracked writers or active leases. The script additionally demands inactive system and ubuntu-user zennay-cloud.service and ubuntu-user chatgpt-firefox.service.
2. Run the read-only preflight after staging the actual consistent database and receipt:

~~~bash
python3 /home/ubuntu/zennay-cloud/ops/promote-final-zcloud-sqlite-new-vps-20261022.py --fence-receipt /home/ubuntu/zcloud-old-writer-fence.json
~~~

3. Reconcile the displayed table counts, recency and existing data with the old source. A smaller incoming count in any key table is rejected. For an existing target, use its exact reviewed target_sha256 from preflight:

~~~bash
python3 /home/ubuntu/zennay-cloud/ops/promote-final-zcloud-sqlite-new-vps-20261022.py --fence-receipt /home/ubuntu/zcloud-old-writer-fence.json --expected-target-sha256 REPLACE_WITH_REVIEWED_TARGET_SHA256 --apply
~~~

If the target history.db does not exist, omit the expected-target hash; the script still requires a real transferred source and valid old fence.

Apply backs up existing target using SQLite online backup to /home/ubuntu/.local/state/zcloud/cutover-backups/, verifies the staged source, detects changed target/source hashes, and uses atomic rename into /home/ubuntu/zennay-cloud/history.db. Existing rollback snapshot is retained and a promotion receipt is recorded. The script does NOT start any API, scheduler, browser session or runner.

4. With separate approval, execute #1583 service and #1578 browser fleet activation, then demonstrate /api/status, /api/portfolio-queue, /api/runner-targets, an actual claim/dispatch/complete event and ten fresh per-slot browser events. Validate public ingress in the separate HTTPS lane.

## Fail-closed behavior

Wrong host/user, symlinks/hardlinks, unreadable or public DB files, pending journal sidecars, invalid SQLite tables/integrity, active or unknown systemd state, absent/stale receipt, source change, target change, or lower incoming table counts all refuse promotion. Data rollback is retained; no automatic rollback after successful atomic rename because new writers might already have committed. No deletion of old VPS data.

Source-only until approved, merged, deployed and executed. No QA/regression suite introduced. Cutover issue: https://github.com/Zennay/zCloud/issues/1588.

# zCloud control-plane recovery runbook

Use this runbook only for the zCloud control plane on the production VPS. It is a recovery path, not a shortcut around the normal guarded deploy lane.

## First response

1. Read health without mutating production:

   `python3 scripts/zcloud_healthcheck.py --json`

2. Inspect the current runtime and last-known-good pointer:

   `python3 scripts/zcloud_recovery.py status`

3. Confirm the exact canonical `main` SHA and its `zcloud/vps-production` status before any forward deploy. Never deploy a stale SHA.

4. If the fault is transient and the existing self-heal path can safely recover it, prefer that bounded recovery over a manual source mutation.

## Preferred forward recovery

The normal recovery path is still the serialized guarded deploy:

`.github/workflows/zcloud-vps-deploy.yml`

Require green regression evidence for current `main`, let the workflow reconfirm `main` immediately before VPS writes, preserve browser safe-idle, transactional promotion, post-deploy health, LKG capture, receipt verification, and final `zcloud/vps-production` status publication.

Do not manually copy repository files into the live tree to bypass these gates.

## Last-known-good rollback

Use rollback when the deployed control-plane source/config is unhealthy and a guarded forward recovery is not the right immediate action:

`python3 scripts/zcloud_recovery.py rollback`

Rollback restores only the recovery layer's managed source/config paths from `~/.local/state/zcloud/recovery/last-known-good.json`. Persistent runtime state such as `history.db`, worker/task mappings, tokens, TLS material, signals, repository metadata, and project repositories must remain preserved.

After rollback, prove recovery again:

- `python3 scripts/zcloud_healthcheck.py --json`
- `python3 scripts/zcloud_recovery.py status`
- confirm the restored LKG carries the expected `POSTDEPLOY_GREEN` evidence;
- verify zCloud is serving the expected runtime and no unrelated project service was altered.

Never delete or rewrite the LKG pointer/snapshots merely to make a rollback pass.

## Fail-closed rules

- Never bypass the current-main prewrite check, safe-idle, transactional promotion, health/canary, LKG, receipt, or production-status gates.
- Never use `git reset --hard`, ad-hoc `cp`/overwrite commands, or direct database replacement as production recovery.
- Never replace `history.db` from a recovery snapshot; it is deliberately persistent.
- Never claim recovery success from service liveness alone. Require the read-only health contract and the expected LKG/post-deploy evidence.
- Do not use FTMO, HaxLab, Raise AI, Supa, LightUp, zSSH, or another project lane as a production writer for zCloud.
- Keep the serialized zCloud production lane authoritative; if another guarded production mutation is active, do not start a competing one.

## Evidence to retain

For every control-plane recovery, retain enough evidence to reconstruct the decision without old chat context:

- canonical `main` SHA;
- `zcloud/vps-production` state and workflow run;
- regression run ID and evidence mode;
- pre-recovery health result;
- recovery `status` output and LKG snapshot ID;
- rollback result when rollback was used;
- post-recovery health result;
- final LKG `POSTDEPLOY_GREEN` evidence and production receipt.

Record the resulting commit/PR/run IDs and the next safe gate in the zCloud Current State & AI Handoff.

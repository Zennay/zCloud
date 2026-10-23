# Portfolio cold storage on the new VPS

This folder provides an isolated **daily offload task**, separate from model training.
Active training, champions, newest checkpoints, SQLite databases, PID/lock files,
working folders and general project files are **never scanned by default**.

## Installation

On the exact host `VM-7eb5ee16-2c71-440d-b7fd-8095d204e930`,
from the current zCloud checkout:

```bash
cd /home/ubuntu/zennay-cloud
git pull --ff-only
sudo bash ops/storage/install-cold-archive.sh
sudo systemctl start zcloud-cold-archive.service
sudo journalctl -u zcloud-cold-archive.service --no-pager -n 60
systemctl list-timers zcloud-cold-archive.timer
```

The service runs as `ubuntu`, reads its policy at `/etc/zcloud-storage/retention.json`,
and accesses the encrypted `vpsarchive:zcloud-vps/automatic/cold-storage`
remote with the existing ubuntu rclone configuration. Once enabled, the timer
runs daily at 03:30 UTC with a randomized 0–15-minute delay.

## What is eligible?

Only files placed under a project-specific, explicitly configured cold directory
(e.g. `archive-ready`) that have remained unchanged for at least 30 or 45 days.
Each run is bounded to at most 100 files and 20 GiB.

Before local deletion, each file is copied to a content-addressed archive object,
the full remote bytes are read back and SHA-256 verified, and a JSON manifest
is written. Changed files and failed uploads remain on the SSD. Missing projects
are skipped. Symlinks and protected live model/database paths are excluded.

Add projects by updating the registry policy and rerunning the installer to refresh
systemd's exact write path allowlist. **Do not** point a cold directory at an
active training directory, database directory or generic project root.

To list candidates without deleting anything:

```bash
sudo -u ubuntu /usr/bin/python3 /usr/local/libexec/zcloud-cold-archive.py \
  --config /etc/zcloud-storage/retention.json \
  --state-dir /var/lib/zcloud-cold-archive
```

To stop automatic offloads (without removing archive data):

```bash
sudo systemctl disable --now zcloud-cold-archive.timer
```

## Restore

Find the object's `destination` and `sha256` in the archived log receipt,
then use `rclone copyto` to restore it to a **new staging path**. Compare
SHA-256 against the manifest before moving it into a live project folder.
The archiver itself never deletes remote archive objects.

## Important scope

This job is an **offload** system, not a general-purpose disk sweeper. Cleanup
of unknown cache directories or still-active models is deliberately not automated.
GitHub runner diagnostics already have a separate protected offloader:
`scripts/zcloud-offload-runner-logs.py`.

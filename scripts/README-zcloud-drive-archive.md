# zCloud cold archive → Google Drive (VPS-only setup)

The user-scoped **rclone v1.75.2** binary is at `/home/ubuntu/.local/bin/rclone`. The
rclone config is `~/.config/rclone/rclone.conf` (mode 0600). It defines
`gdrive:` (Google Drive, restricted `drive.file` scope) and
`vpsarchive:` (rclone crypt, encrypted filenames/content, rooted at
`gdrive:VPS-Archive-Encrypted`).

## One-time account authorization (not completed)

1. Connect to the VPS as **ubuntu**, not root. Use the terminal in the VPS's
   existing graphical desktop, or rclone's official headless authorize flow.
2. Create your **own Google OAuth desktop client ID** via Google Cloud Console
   and use it to edit `gdrive` with `~/.local/bin/rclone config`. As of 2026
   the shared rclone client ID is being retired; do not depend on it.
3. Authorize the VPS remote using
   `~/.local/bin/rclone config reconnect gdrive:`. Do not post a token,
   client secret, or config file in a chat, issue, Git repository or log.
4. Verify `~/.local/bin/rclone lsd gdrive:` completes without an auth error.
   Make sure the destination account has enough storage quota.

A ChatGPT **Google Drive plugin connection is separate** from the VPS rclone
OAuth. Connecting one does not supply credentials to the other.

## Cold/hot policy

Hot and excluded: live zCloud `server.py`, SQLite/WAL, `.git`, `repos/`,
`worktrees/`, active worker/Firefox data, Actions runners, databases, HaxLab
training/input, FTMO trading/research data. These are not uploaded/moved by
this policy.

Cold: *top-level* date-stamped zCloud task/backup directories only, with
no nested file, directory or symlink modified in the last 2 days. Bounded
to 8 directories or 4 GiB per daily run, with a single bandwidth-limited
transfer. Copies are checked by downloading and comparing the remote bytes.
The source is **never** deleted by the ordinary archive run.

The separate weekly cleanup examines directories with no modifications
for at least **7 days**, copies/rechecks all bytes, verifies the selection
again and rejects working-directory process references. It only removes an
original when the file `~/.config/zcloud-drive/allow-prune` exists.

## Critical recovery-key safeguard

Unique encryption recovery material is in
`~/.config/zcloud-drive/recovery-keys.txt` (0600). You **must secure a copy
outside this VPS**, in a trustworthy offline password manager/backup, before
creating `~/.config/zcloud-drive/allow-prune`. If the VPS disappears and
you do not have the rclone crypt passwords (and your Google authorization),
you cannot restore encrypted files.

## Operations

- Dry plan: `python3 -B ~/zennay-cloud/scripts/zcloud-drive-archive.py plan`
- Archive now (after OAuth):
  `python3 -B ~/zennay-cloud/scripts/zcloud-drive-archive.py run`
- Cleanup after offsite key backup and explicit marker:
  `python3 -B ~/zennay-cloud/scripts/zcloud-drive-archive.py cleanup`
- Restore a directory (after OAuth):
  `~/.local/bin/rclone copy "vpsarchive:zCloud-cold/DIR" "/home/ubuntu/zennay-cloud/DIR"`

User services `zcloud-drive-archive.timer` (daily 03:15 Amsterdam time)
and `zcloud-drive-cleanup.timer` (Sunday 04:45) are installed and enabled.
The cleanup service has `ConditionPathExists` for the explicit approval marker.
Transfers and pruning must not be mistaken for main merges or runtime recovery.

This system is intentionally **not** a full-drive mount or automatic deletion
of databases, caches, working Git trees or volatile runtime data. Disk space
will not improve until Google authorization, validated remote copies and
approved pruning have occurred.

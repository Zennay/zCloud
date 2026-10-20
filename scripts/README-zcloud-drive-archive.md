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

Cold: only *top-level, explicitly zCloud-owned* directory names:
`zcloud-cold-YYYYMMDD`, `zcloud-archive-YYYYMMDD`,
`zcloud-backup-YYYYMMDD`, optionally followed by `-suffix` or `_suffix`
(ASCII letters, digits, underscores and hyphens). The date must be at
least two days old in UTC, and the whole tree must be idle for two days.
A date elsewhere in a directory name **does not grant upload permission**:
this host has hundreds of date-marked FTMO, HaxLab, LightUp and other
project working directories that the former selector could accidentally
upload. No existing private, research or unrelated project directory is
automatically eligible just because it is old.

The bounded scanner refuses symlinks, hard-linked or nonregular files, nested
filesystems and trees larger than 4 GiB or 100,000 visited inodes. The
selection is limited to 8 directories and 4 GiB per run. Copies are
checked by **downloading and comparing remote bytes and the entire directory
content in both directions**. Extra old remote files invalidate the verified
archive rather than silently reappearing on restore. The source is **never**
deleted by the ordinary archive run.

`plan` is genuinely read-only: it never creates the state/lock directory,
checks OAuth or changes any data. It prints bounded eligible directory names,
file/byte counts and explicitly `transfer_performed=false` /
`cleanup_authorized=false`. If there are no eligible zCloud-owned
directories, `run` and `cleanup` report that fact and do **not** try to
archive unrelated dated directories. Remote authentication failures, by
contrast, produce a nonzero process exit so enabled timers cannot claim
successful uploads.

The separate weekly cleanup examines directories with no modifications
**or inode metadata changes** for at least **7 days**. Its selected tree
gets a stable fingerprint of file/directory identity, size, link count,
mtime and ctime. After the remote copy and exact two-way downloaded check,
cleanup scans again and refuses deletion if the entire fingerprint changed,
even if total bytes and file count still match. It rejects processes holding
an executable, cwd **or open file descriptor** under the directory; if
`/proc` permissions make that check inconclusive, deletion is denied.
This reduces but cannot eliminate changes racing with a deletion; keep
active writers out of the cold archive roots.

Pruning additionally requires the original
`~/.config/zcloud-drive/allow-prune` marker to be an actual regular,
**user-owned mode-0600 file** (no symlink), with the recovery keys already
backed up offsite. The marker is rechecked immediately before deletion.
No cleanup is run or authorized by this source change.

## Critical recovery-key safeguard

Unique encryption recovery material is in
`~/.config/zcloud-drive/recovery-keys.txt` (0600). You **must secure a copy
outside this VPS**, in a trustworthy offline password manager/backup, before
creating `~/.config/zcloud-drive/allow-prune`. If the VPS disappears and
you do not have the rclone crypt passwords (and your Google authorization),
you cannot restore encrypted files. Create the approval marker only after
an authorized operator has reviewed the exact cold directory scope and
backed up those keys; for example as the ubuntu account:
`install -m 0600 /dev/null ~/.config/zcloud-drive/allow-prune`.
Do not create that marker for a demo, smoke check or unverified provider.

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

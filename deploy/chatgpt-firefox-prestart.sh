#!/usr/bin/env bash
set -euo pipefail
PROFILE="${ZCLOUD_FIREFOX_PROFILE:-/home/ubuntu/snap/firefox/common/chatgpt-webext-profile}"
SOURCE="${ZCLOUD_FIREFOX_SOURCE:-/home/ubuntu/zennay-cloud/firefox-extension}"
RUNTIME="${ZCLOUD_FIREFOX_RUNTIME:-/home/ubuntu/snap/firefox/common/chatgpt-project-extension}"

# systemd already owns the worker browser's process group. Never run the
# historical chatgpt-firefox-stop.sh here: a broad Firefox kill could also
# terminate the independent Office Relay browser and other logged-in sessions.
# Refuse to touch a profile held by a live Firefox process instead.
python3 - "$PROFILE" <<'PY'
import os
from pathlib import Path
import sys

profile = os.path.realpath(sys.argv[1]).encode()
for process in Path("/proc").iterdir():
    if not process.name.isdigit() or int(process.name) == os.getpid():
        continue
    try:
        cmdline = (process / "cmdline").read_bytes().replace(b"\x00", b" ")
    except (PermissionError, ProcessLookupError, FileNotFoundError):
        continue
    if profile in cmdline and b"firefox" in cmdline.lower():
        raise SystemExit("Worker Firefox-profiel is nog in gebruik; bestaande tabs niet overnemen")
PY

mkdir -p "$PROFILE" "$RUNTIME"
rsync -a --delete --exclude='*.bak-*' "$SOURCE/" "$RUNTIME/"

# After an actual browser crash the lock files can be stale. Remove only
# these locks, not Firefox sessionstore.jsonlz4/sessionstore-backups. Keeping
# those files is essential for reopening the original worker tabs and their
# browser.sessions worker-id bindings after a restart.
rm -f "$PROFILE/lock" "$PROFILE/.parentlock" "$PROFILE/parent.lock"

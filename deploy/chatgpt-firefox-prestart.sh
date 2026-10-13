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

# Apply conservative, reversible worker-profile memory caps on the NEXT launch.
# This script never restarts Firefox or unloads an active/background worker tab.
# Standalone Firefox users may opt in by running the tuner on a *stopped* profile.
if [[ "${ZCLOUD_FIREFOX_MEMORY_CAPS:-1}" == "1" ]]; then
    TUNER="${ZCLOUD_FIREFOX_MEMORY_CAPS_SCRIPT:-/home/ubuntu/zennay-cloud/scripts/zcloud_firefox_memory_caps.py}"
    if [[ -f "$TUNER" ]]; then
        if ! python3 "$TUNER" --profile "$PROFILE"; then
            echo "warning: Firefox memory caps not applied; preserving worker startup" >&2
        fi
    else
        echo "warning: Firefox memory cap installer not deployed: $TUNER" >&2
    fi
fi

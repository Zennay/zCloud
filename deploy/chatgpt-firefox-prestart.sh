#!/usr/bin/env bash
set -euo pipefail
PROFILE=/home/ubuntu/snap/firefox/common/chatgpt-webext-profile
SOURCE=/home/ubuntu/zennay-cloud/firefox-extension
RUNTIME=/home/ubuntu/snap/firefox/common/chatgpt-project-extension
STOP=/home/ubuntu/.local/bin/chatgpt-firefox-stop.sh

[ ! -x "$STOP" ] || "$STOP"
mkdir -p "$PROFILE" "$RUNTIME"
rsync -a --delete --exclude='*.bak-*' "$SOURCE/" "$RUNTIME/"
rm -f "$PROFILE/lock" "$PROFILE/.parentlock" "$PROFILE/parent.lock"
rm -f "$PROFILE/sessionstore.jsonlz4"
rm -rf "$PROFILE/sessionstore-backups"

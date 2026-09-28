#!/usr/bin/env bash
set -euo pipefail
FILE="${1:-firefox-extension/background.js}"
node --check "$FILE"
grep -q 'ZCLOUD_AUTONOMY: WAIT_VPS' "$FILE"
grep -q 'autonomy-wait-human' "$FILE"
grep -q 'autoContinueDelayMs' "$FILE"
grep -q 'autonomy-cooldown-complete' "$FILE"
grep -q 'policy-unavailable' "$FILE"

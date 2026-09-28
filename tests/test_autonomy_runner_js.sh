#!/usr/bin/env bash
set -euo pipefail
FILE="${1:-firefox-extension/background.js}"
node --check "$FILE"
grep -q 'ZCLOUD_AUTONOMY: WAIT_VPS' "$FILE"
grep -q 'autonomy-wait-human' "$FILE"
grep -q 'autonomy-priority' "$FILE"
grep -q 'ZCLOUD_PRIORITY:' "$FILE"
grep -q 'awaiting-vps-dispatch' "$FILE"
grep -q 'target.active === true' "$FILE"
grep -q 'Math.max(0, Number(cfg.auto_continue_delay_seconds ?? 0)' "$FILE"
grep -q 'setInterval(refreshTargets, 5000)' "$FILE"
grep -q 'policy-unavailable' "$FILE"

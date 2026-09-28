#!/usr/bin/env bash
set -euo pipefail
FILE="${1:-firefox-extension/background.js}"
node --check "$FILE"
grep -q 'runner-policy-check' "$FILE"
grep -q 'autoContinue = false' "$FILE"
grep -q 'improvement-iteration-complete' "$FILE"
grep -q 'improvement-review-green' "$FILE"
grep -q 'improvement-audit-green' "$FILE"
grep -q 'auto-continue-blocked' "$FILE"
grep -q 'status.auto_continue === false' "$FILE"
echo "Self-improvement browser stop-gate checks passed."

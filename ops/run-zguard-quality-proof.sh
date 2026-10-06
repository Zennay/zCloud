#!/usr/bin/env bash
set -euo pipefail

quality_sha="3e60b2399daa1d82d9b537a17b7690225b4f4651"
settings_sha="234d76d005ff2e47b3a1dacea53378f7e921e48e"
work="${RUNNER_TEMP:?}/zguard-quality-proof-${GITHUB_RUN_ID:?}-${GITHUB_RUN_ATTEMPT:?}"

rm -rf "$work"
git clone --quiet https://github.com/Zennay/zGuard.git "$work"

git -C "$work" checkout --quiet --detach "$quality_sha"
test "$(git -C "$work" rev-parse HEAD)" = "$quality_sha"
node "$work/tests/smoke.test.js"
bash "$work/zbrowse/scripts/validate.sh"
(
  cd "$work/zbrowse/gateway"
  npm ci --omit=dev --ignore-scripts --no-audit
  npm audit --omit=dev --audit-level=high
)

rm -rf "$work/zbrowse/gateway/node_modules"
git -C "$work" checkout --quiet --detach "$settings_sha"
test "$(git -C "$work" rev-parse HEAD)" = "$settings_sha"
node "$work/tests/smoke.test.js"
bash "$work/zbrowse/scripts/validate.sh"

echo "zGuard external VPS quality proof passed"

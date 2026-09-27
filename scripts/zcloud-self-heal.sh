#!/usr/bin/env bash
set -euo pipefail

SERVICE="${ZCLOUD_SERVICE:-zennay-cloud.service}"
URL="${ZCLOUD_HEALTH_URL:-http://127.0.0.1:8765/api/status}"
DISABLE_FILE="${ZCLOUD_SELF_HEAL_DISABLE_FILE:-/home/ubuntu/zennay-cloud/.disable-self-heal}"
LOCK_FILE="${ZCLOUD_SELF_HEAL_LOCK_FILE:-/run/zcloud-self-heal.lock}"

[[ -e "${DISABLE_FILE}" ]] && exit 0

exec 9>"${LOCK_FILE}"
flock -n 9 || exit 0

healthy() {
  systemctl is-active --quiet "${SERVICE}" &&
    curl -fsS --max-time 5 "${URL}" >/dev/null
}

if healthy; then
  exit 0
fi

logger -t zcloud-self-heal "health failed; restarting ${SERVICE}"
systemctl restart "${SERVICE}"

for _ in {1..20}; do
  if healthy; then
    logger -t zcloud-self-heal "recovery succeeded"
    exit 0
  fi
  sleep 1
done

logger -t zcloud-self-heal "recovery failed after restart"
exit 1

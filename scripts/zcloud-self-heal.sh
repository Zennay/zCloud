#!/usr/bin/env bash
set -euo pipefail

SERVICE="${ZCLOUD_SERVICE:-zennay-cloud.service}"
URL="${ZCLOUD_HEALTH_URL:-http://127.0.0.1:8765/api/status}"
DISABLE_FILE="${ZCLOUD_SELF_HEAL_DISABLE_FILE:-/home/ubuntu/zennay-cloud/.disable-self-heal}"
RUNTIME_DISABLE_DIR="${ZCLOUD_RUNTIME_HEAL_DISABLE_DIR:-/home/ubuntu/zennay-cloud/.disable-runtime-heal}"
LOCK_FILE="${ZCLOUD_SELF_HEAL_LOCK_FILE:-/run/zcloud-self-heal.lock}"

[[ -e "${DISABLE_FILE}" ]] && exit 0

exec 9>"${LOCK_FILE}"
flock -n 9 || exit 0

healthy() {
  systemctl is-active --quiet "${SERVICE}" &&
    curl -fsS --max-time 5 "${URL}" >/dev/null
}

heal_zcloud() {
  if healthy; then
    return 0
  fi

  logger -t zcloud-self-heal "health failed; restarting ${SERVICE}"
  systemctl restart "${SERVICE}"

  for _ in {1..20}; do
    if healthy; then
      logger -t zcloud-self-heal "recovery succeeded"
      return 0
    fi
    sleep 1
  done

  logger -t zcloud-self-heal "recovery failed after restart"
  return 1
}

runtime_disabled() {
  local service="$1"
  [[ -e "${RUNTIME_DISABLE_DIR}/${service}" ]]
}

heal_system_unit() {
  local service="$1"
  runtime_disabled "${service}" && return 0
  systemctl cat "${service}" >/dev/null 2>&1 || return 0

  if ! systemctl is-enabled --quiet "${service}" 2>/dev/null; then
    logger -t zcloud-runtime-heal "enabling system unit ${service}"
    systemctl enable "${service}" >/dev/null
  fi
  if ! systemctl is-active --quiet "${service}"; then
    logger -t zcloud-runtime-heal "starting system unit ${service}"
    systemctl start "${service}"
  fi
}

user_systemctl() {
  local uid="${ZCLOUD_RUNTIME_USER_UID:-1000}"
  XDG_RUNTIME_DIR="/run/user/${uid}" \
  DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/${uid}/bus" \
    systemctl --user "$@"
}

heal_user_unit() {
  local service="$1"
  runtime_disabled "${service}" && return 0
  user_systemctl cat "${service}" >/dev/null 2>&1 || return 0

  if ! user_systemctl is-enabled --quiet "${service}" 2>/dev/null; then
    logger -t zcloud-runtime-heal "enabling user unit ${service}"
    user_systemctl enable "${service}" >/dev/null
  fi
  if ! user_systemctl is-active --quiet "${service}"; then
    logger -t zcloud-runtime-heal "starting user unit ${service}"
    user_systemctl start "${service}"
  fi
}

heal_project_runtimes() {
  mkdir -p "${RUNTIME_DISABLE_DIR}"

  # Long-lived system services/timers that should remain alive independently
  # from ChatGPT worker state. Missing units are intentionally ignored.
  for service in \
    haxlab-ingest.service \
    haxlab-worker.service \
    haxlab-analyzer.service \
    haxlab-autonomy.timer \
    ftmo-autonomous.timer
  do
    heal_system_unit "${service}"
  done

  # User services owned by the ubuntu runtime account.
  for service in raise-gateway.service zssh.service; do
    heal_user_unit "${service}"
  done
}

heal_zcloud
heal_project_runtimes

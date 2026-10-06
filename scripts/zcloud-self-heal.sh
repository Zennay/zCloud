#!/usr/bin/env bash
set -euo pipefail

SERVICE="${ZCLOUD_SERVICE:-zennay-cloud.service}"
URL="${ZCLOUD_HEALTH_URL:-http://127.0.0.1:8765/}"
DISABLE_FILE="${ZCLOUD_SELF_HEAL_DISABLE_FILE:-/home/ubuntu/zennay-cloud/.disable-self-heal}"
RUNTIME_DISABLE_DIR="${ZCLOUD_RUNTIME_HEAL_DISABLE_DIR:-/home/ubuntu/zennay-cloud/.disable-runtime-heal}"
RUNTIME_USER="${ZCLOUD_RUNTIME_USER:-ubuntu}"
RUNTIME_USER_UID="${ZCLOUD_RUNTIME_USER_UID:-1000}"
LOCK_FILE="${ZCLOUD_SELF_HEAL_LOCK_FILE:-/run/zcloud-self-heal.lock}"
WORKER_WATCHDOG="${ZCLOUD_WORKER_WATCHDOG:-/usr/local/sbin/zcloud-worker-watchdog}"

[[ -e "${DISABLE_FILE}" ]] && exit 0

exec 9>"${LOCK_FILE}"
flock -n 9 || exit 0

healthy() {
  systemctl is-active --quiet "${SERVICE}" &&
    curl -fsS --max-time 8 "${URL}" >/dev/null
}

heal_zcloud() {
  if healthy; then
    return 0
  fi

  # A live status/read-model request can briefly monopolise the Python process
  # long enough for the 8s root probe to time out. Do not turn one slow sample
  # into a restart loop: allow the in-flight request to finish and confirm that
  # the lightweight root endpoint is still unavailable before recycling zCloud.
  if systemctl is-active --quiet "${SERVICE}"; then
    logger -t zcloud-self-heal "health probe timed out; confirming before restart"
    sleep 3
    if healthy; then
      logger -t zcloud-self-heal "health recovered during confirmation window"
      return 0
    fi
  fi

  logger -t zcloud-self-heal "health failed twice; restarting ${SERVICE}"
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
  local runtime_dir="/run/user/${RUNTIME_USER_UID}"
  local bus="unix:path=${runtime_dir}/bus"
  if [[ "${EUID}" -eq 0 ]]; then
    runuser -u "${RUNTIME_USER}" -- env \
      XDG_RUNTIME_DIR="${runtime_dir}" \
      DBUS_SESSION_BUS_ADDRESS="${bus}" \
      systemctl --user "$@"
  else
    XDG_RUNTIME_DIR="${runtime_dir}" \
    DBUS_SESSION_BUS_ADDRESS="${bus}" \
      systemctl --user "$@"
  fi
}

legacy_violentmonkey_only() {
  local runtime_home
  runtime_home="$(getent passwd "${RUNTIME_USER}" 2>/dev/null | cut -d: -f6 || true)"
  [[ -n "${runtime_home}" ]] || runtime_home="/home/${RUNTIME_USER}"
  local marker="${runtime_home}/.config/systemd/user/chatgpt-firefox.service.d/10-legacy-disabled.conf"
  [[ -f "${marker}" ]] &&
    grep -qi 'violentmonkey only' "${marker}" &&
    grep -q 'ExecCondition=/bin/false' "${marker}"
}

heal_user_unit() {
  local service="$1"
  runtime_disabled "${service}" && return 0
  # In userscript-only mode the systemd Firefox unit is intentionally disabled.
  # The worker-progress watchdog owns liveness/restart of the standalone browser.
  if [[ "${service}" == "chatgpt-firefox.service" ]] && legacy_violentmonkey_only; then
    return 0
  fi
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

heal_zcloud_actions_runner() {
  local runner_name="${ZCLOUD_ACTIONS_RUNNER_NAME:-zcloud-vps-1}"
  local runner_user="${ZCLOUD_ACTIONS_RUNNER_USER:-ubuntu}"
  local runner_root="${ZCLOUD_ACTIONS_RUNNER_ROOT:-/home/ubuntu/actions-runner-zcloud}"
  local -a units=()

  mapfile -t units < <(
    systemctl list-unit-files --type=service --no-legend 'actions.runner.*' 2>/dev/null \
      | awk '{print $1}' \
      | grep -F "${runner_name}" || true
  )

  if [[ "${#units[@]}" -eq 0 ]]; then
    logger -t zcloud-runner-heal "no Actions runner unit matched ${runner_name}; leaving runtime unchanged"
    return 0
  fi
  if [[ "${#units[@]}" -ne 1 ]]; then
    logger -t zcloud-runner-heal "ambiguous Actions runner units for ${runner_name}: ${units[*]}"
    return 0
  fi

  local unit="${units[0]}"
  runtime_disabled "${unit}" && return 0

  if systemctl is-active --quiet "${unit}"; then
    return 0
  fi

  if pgrep -u "${runner_user}" -f "${runner_root}/.*/Runner\\.Worker|${runner_root}/bin/Runner\\.Worker" >/dev/null; then
    logger -t zcloud-runner-heal "Actions Runner.Worker is active; preserving ${unit}"
    return 0
  fi

  sleep 3
  if systemctl is-active --quiet "${unit}"; then
    return 0
  fi
  if pgrep -u "${runner_user}" -f "${runner_root}/.*/Runner\\.Worker|${runner_root}/bin/Runner\\.Worker" >/dev/null; then
    logger -t zcloud-runner-heal "Actions Runner.Worker appeared during guard window; preserving ${unit}"
    return 0
  fi

  logger -t zcloud-runner-heal "starting inactive zCloud Actions runner ${unit}"
  systemctl reset-failed "${unit}" || true
  if ! systemctl start "${unit}"; then
    logger -t zcloud-runner-heal "failed to start zCloud Actions runner ${unit}; retrying next timer tick"
    return 0
  fi

  for _ in {1..10}; do
    if systemctl is-active --quiet "${unit}"; then
      return 0
    fi
    sleep 1
  done

  logger -t zcloud-runner-heal "zCloud Actions runner ${unit} did not become active; retrying next timer tick"
  return 0
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
    ftmo-autonomous-marathon.service
  do
    heal_system_unit "${service}"
  done

  # User services owned by the ubuntu runtime account. Recover the browser
  # dependency chain first; otherwise runner commands can remain pending forever
  # while zCloud itself still looks healthy.
  for service in \
    chatgpt-display.service \
    chatgpt-openbox.service \
    chatgpt-firefox.service \
    raise-gateway.service \
    zssh.service
  do
    heal_user_unit "${service}"
  done
}

heal_worker_progress() {
  [[ -x "${WORKER_WATCHDOG}" ]] || return 0

  # Keep every SQLite writer on the same runtime identity as zennay-cloud.service.
  # The self-heal unit itself runs as root so it can recover system services; if
  # the watchdog also runs as root, SQLite may create history.db-wal/-shm owned
  # by root and the ubuntu-owned zCloud process then fails with SQLITE_READONLY.
  local runtime_home state_dir watchdog_state
  runtime_home="$(getent passwd "${RUNTIME_USER}" 2>/dev/null | cut -d: -f6 || true)"
  [[ -n "${runtime_home}" ]] || runtime_home="/home/${RUNTIME_USER}"
  state_dir="${runtime_home}/.local/state/zcloud"
  watchdog_state="${state_dir}/worker-progress-watchdog.json"

  if [[ "${EUID}" -eq 0 ]]; then
    install -d -o "${RUNTIME_USER}" -g "$(id -gn "${RUNTIME_USER}")" -m 0755 "${state_dir}"
    if ! runuser -u "${RUNTIME_USER}" -- env \
      HOME="${runtime_home}" \
      ZCLOUD_WORKER_WATCHDOG_STATE="${watchdog_state}" \
      "${WORKER_WATCHDOG}" --json; then
      logger -t zcloud-worker-watchdog "watchdog invocation failed; retrying on next timer tick"
      return 0
    fi
  elif ! ZCLOUD_WORKER_WATCHDOG_STATE="${watchdog_state}" "${WORKER_WATCHDOG}" --json; then
    logger -t zcloud-worker-watchdog "watchdog invocation failed; retrying on next timer tick"
    return 0
  fi
}

heal_zcloud
heal_zcloud_actions_runner
heal_project_runtimes
heal_worker_progress

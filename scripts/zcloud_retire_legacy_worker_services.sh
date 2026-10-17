#!/usr/bin/env bash
# Retire only superseded zCloud browser automation; preserve Firefox and runner.
set -euo pipefail
test "$(hostname)" = vps-bb300bba
test "$(id -un)" = ubuntu
export XDG_RUNTIME_DIR=/run/user/1000
export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus
retire() {
  local scope="$1" stem="$2"
  local -a manager=(systemctl --user)
  if [[ "$scope" == system ]]; then manager=(sudo -n systemctl); fi
  local unit load active
  for unit in "$stem.timer" "$stem.service"; do
    load="$("${manager[@]}" show "$unit" --property=LoadState --value)" || [[ "$load" == not-found ]]
    if [[ "$load" == not-found ]]; then continue; fi
    if [[ "$unit" == *.timer ]]; then
      "${manager[@]}" disable --now "$unit"
    else
      "${manager[@]}" stop "$unit"
    fi
    active="$("${manager[@]}" show "$unit" --property=ActiveState --value)"
    [[ "$active" == inactive || "$active" == failed ]]
    printf 'RETIRED_LEGACY_UNIT=%s:%s:%s\n' "$scope" "$unit" "$active"
  done
}
retire user chatgpt-runner-watchdog
retire user zcloud-watchdog
# Keep zcloud-self-heal.timer active: it owns Actions runner liveness and
# the memory-pressure worker watchdog. Retire only legacy prompt timers.
# Keep the independent real crash recovery owner.
systemctl is-active zcloud-firefox-supervisor.timer

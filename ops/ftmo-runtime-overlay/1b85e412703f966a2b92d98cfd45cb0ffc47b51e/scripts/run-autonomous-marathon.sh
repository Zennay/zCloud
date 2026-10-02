#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-/opt/ftmo-autonomous}"
PY="${FTMO_PYTHON:-$ROOT/.venv/bin/python}"
CONFIG="$ROOT/configs/autonomous_runtime.json"

test -x "$PY"
test -f "$CONFIG"
test "${FTMO_AUTONOMOUS_CLOUD_BUDGET_USD:-0.00}" = "0.00"
test -z "${FTMO_ALLOW_PAID_AWS:-}"

while true; do
  set +e
  output="$("$PY" -m prop_trading_ai.cli.v1_autonomous_tick --root "$ROOT" --config "$CONFIG" 2>&1)"
  rc=$?
  set -e

  if [[ "$rc" -eq 0 ]]; then
    printf '%s\n' "$output"
    if printf '%s\n' "$output" | grep -q '"action": "provider_foundation_provider_unavailable_retry_later"'; then
      echo "FTMO research provider unavailable; outcomes remain sealed, backing off provider retry" >&2
      sleep "${FTMO_MARATHON_RESEARCH_PROVIDER_RETRY_SECONDS:-900}"
    else
      sleep 2
    fi
    continue
  fi

  if printf '%s\n' "$output" | grep -q "another autonomous tick is already running"; then
    echo "FTMO autonomous lock busy; retrying"
    sleep 5
    continue
  fi

  # A rejected secondary feed is a fail-closed research gate, not a reason for
  # the persistent supervisor itself to die. Keep the runtime alive at low
  # cadence while the P0 repair worker fixes/reconciles provider evidence.
  if printf '%s\n' "$output" | grep -q "secondary-feed validation did not pass"; then
    printf '%s\n' "$output" >&2
    echo "FTMO safe provider gate blocked; keeping marathon alive and retrying later" >&2
    sleep "${FTMO_MARATHON_GATE_RETRY_SECONDS:-60}"
    continue
  fi

  printf '%s\n' "$output" >&2
  exit "$rc"
done

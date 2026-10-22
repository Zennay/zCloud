#!/usr/bin/env bash
# Recover only the private zSSH *user* service on the new VPS.
# Read-only by default; --apply starts an inactive matching service.
set -Eeuo pipefail

EXPECTED_HOST='VM-7eb5ee16-2c71-440d-b7fd-8095d204e930'
EXPECTED_UNIT='/home/zssh/.config/systemd/user/zssh.service'
EXPECTED_APP='/home/zssh/.local/share/zssh/current/server.mjs'
EXPECTED_ENV='/home/zssh/.config/zssh/gateway.env'
URL='http://127.0.0.1:8788/health'

usage() {
  printf 'Usage: %s [--status|--apply]\n' "$0" >&2
  exit 2
}

[[ $# -le 1 ]] || usage
MODE="${1:---status}"
[[ "$MODE" == '--status' || "$MODE" == '--apply' ]] || usage
[[ "$(hostname)" == "$EXPECTED_HOST" && "$EUID" -eq 0 ]] || {
  echo 'REFUSED: root on the exact new VPS is required' >&2
  exit 2
}
[[ "$(id -u zssh 2>/dev/null || echo 0)" -gt 0 ]] || {
  echo 'REFUSED: unprivileged zssh service user is missing' >&2
  exit 2
}
[[ -f "$EXPECTED_UNIT" && ! -L "$EXPECTED_UNIT" &&
   -f "$EXPECTED_APP" && -f "$EXPECTED_ENV" ]] || {
  echo 'REFUSED: expected private user service installation is incomplete' >&2
  exit 2
}
# This is not the public OAuth gateway at 8789. Do not redirect or restart it.
grep -Eq '^PORT=8788$' "$EXPECTED_ENV" || {
  echo 'REFUSED: private gateway port is not configured as 8788' >&2
  exit 2
}

unit_show() {
  systemctl --user -M zssh@ show zssh.service -p "$1" --value
}

fragment="$(unit_show FragmentPath)"
execstart="$(unit_show ExecStart)"
loadstate="$(unit_show LoadState)"
active="$(unit_show ActiveState)"

[[ "$loadstate" == 'loaded' && "$fragment" == "$EXPECTED_UNIT" &&
   "$execstart" == *"$EXPECTED_APP"* ]] || {
  echo 'REFUSED: zssh.service identity differs from expected unprivileged private gateway' >&2
  exit 2
}

healthy() {
  curl -fsS --connect-timeout 1 --max-time 3 "$URL" >/dev/null 2>&1
}

echo "host=$EXPECTED_HOST mode=$MODE"
echo "private_zssh_load=$loadstate private_zssh_state=$active"
echo "private_zssh_unit=$fragment"
echo "private_zssh_health=$(if healthy; then echo healthy; else echo unavailable; fi)"

if [[ "$MODE" == '--status' ]]; then
  echo 'STATUS_ONLY: no services or files changed.'
  exit 0
fi

if [[ "$active" == 'active' ]]; then
  if healthy; then
    echo 'NOOP: private user service already active and healthy.'
    exit 0
  fi
  echo 'REFUSED: service is active but unhealthy; no automated restart of a live service.' >&2
  exit 3
fi

[[ "$active" == 'inactive' || "$active" == 'failed' ]] || {
  echo "REFUSED: service state $active is not eligible for a start" >&2
  exit 3
}

if ss -H -ltn '( sport = :8788 )' | grep -q .; then
  echo 'REFUSED: port 8788 is occupied by another listener' >&2
  exit 3
fi

# Explicit operator action only. Do not touch cloned GitHub runners, the
# public OAuth service, Caddy, zCloud, Firefox, or training processes.
loginctl enable-linger zssh
systemctl --user -M zssh@ enable zssh.service
systemctl --user -M zssh@ start zssh.service

for ((attempt=1; attempt<=30; attempt++)); do
  if healthy; then
    echo 'PRIVATE_ZSSH_RECOVERED: localhost:8788 health OK'
    echo 'No public ingress/DNS or other services changed.'
    exit 0
  fi
  sleep 1
done

echo 'ERROR: private user service was started but did not become healthy.' >&2
echo 'Inspect: systemctl --user -M zssh@ status zssh.service --no-pager' >&2
exit 4

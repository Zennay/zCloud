#!/usr/bin/env bash
# New VPS base-service activation. Never modify GitHub runner registrations,
# worker processes, SQLite, training, browser sessions, or public ingress.
# Read-only by default. --apply only enables *existing* Docker/archive units.
set -Eeuo pipefail

EXPECTED_HOST='VM-7eb5ee16-2c71-440d-b7fd-8095d204e930'
ARCHIVE_UNIT='zcloud-external-archive.service'
DOCKER_UNIT='docker.service'

usage() {
  printf 'Usage: %s [--status|--apply]\n' "$0" >&2
  exit 2
}

[[ $# -le 1 ]] || usage
MODE="${1:---status}"
[[ "$MODE" == '--status' || "$MODE" == '--apply' ]] || usage
[[ "$(hostname)" == "$EXPECTED_HOST" && "$EUID" -eq 0 ]] || {
  echo 'REFUSED: this command requires root on the exact new VPS' >&2
  exit 2
}

unit_load() {
  systemctl show "$1" -p LoadState --value 2>/dev/null || true
}

unit_active() {
  systemctl is-active "$1" 2>/dev/null || true
}

docker_api_version() {
  command -v docker >/dev/null 2>&1 || return 1
  command -v timeout >/dev/null 2>&1 || return 1
  timeout 10s docker info --format '{{.ServerVersion}}' 2>/dev/null
}

echo "=== NEW VPS BASE SERVICES: $MODE ==="
echo "host=$EXPECTED_HOST"
echo "archive=$(unit_load "$ARCHIVE_UNIT")/$(unit_active "$ARCHIVE_UNIT")"
echo "docker=$(unit_load "$DOCKER_UNIT")/$(unit_active "$DOCKER_UNIT")"
if DOCKER_VERSION="$(docker_api_version)" && [[ -n "$DOCKER_VERSION" ]]; then
  echo "docker_api=reachable/$DOCKER_VERSION"
else
  echo "docker_api=unavailable"
fi

echo '=== GITHUB RUNNER UNITS (OBSERVE ONLY) ==='
mapfile -t RUNNERS < <(
  systemctl list-unit-files --type=service --no-legend --no-pager \
    'actions.runner.*.service' 2>/dev/null |
    awk '$1 ~ /^actions[.]runner[.]/ {print $1}'
)
echo "detected_runner_units=${#RUNNERS[@]}"
for unit in "${RUNNERS[@]}"; do
  echo "$unit $(unit_active "$unit")"
done
echo 'PRESERVED: no runner stop, disable, enable, registration or credential changes.'

echo '=== LOCAL RESTORE PRESENCE ==='
for path in \
  /home/ubuntu/zennay-cloud \
  /home/ubuntu/.mozilla/firefox \
  /home/ubuntu/actions-runner-zcloud
do
  if [[ -e "$path" ]]; then
    echo "PRESENT $path"
  else
    echo "MISSING $path"
  fi
done

echo '=== STORAGE (OBSERVE ONLY) ==='
df -h /
findmnt -rn -o TARGET,SOURCE,FSTYPE |
  awk '$1 ~ /VPS-Archief/ || $3 ~ /^fuse[.]rclone$/ {print}' || true
echo 'IMPORTANT: the read-only archive is not a writable external Fuckball training disk.'

echo '=== OTHER SERVICE STATES (OBSERVE ONLY) ==='
for unit in \
  zennay-cloud.service \
  zcloud-firefox-supervisor.service \
  zcloud-self-heal.service \
  zennay-portfolio-runner-pool.service
do
  echo "$unit $(unit_load "$unit")/$(unit_active "$unit")"
done

if [[ "$MODE" == '--status' ]]; then
  echo 'STATUS_ONLY: no files, services, runners or containers changed.'
  exit 0
fi

# Fail before any mutation if prerequisites are not already installed.
[[ "$(unit_load "$ARCHIVE_UNIT")" == 'loaded' ]] || {
  echo 'REFUSED: external archive service not installed' >&2
  exit 3
}
[[ "$(unit_load "$DOCKER_UNIT")" == 'loaded' ]] || {
  echo 'REFUSED: Docker systemd unit is not installed' >&2
  exit 3
}
command -v docker >/dev/null 2>&1 || {
  echo 'REFUSED: Docker CLI missing; no automatic package installation' >&2
  exit 3
}
command -v timeout >/dev/null 2>&1 || {
  echo 'REFUSED: timeout utility missing; cannot bound Docker API inspection' >&2
  exit 3
}

# Explicit, narrowly scoped operator action. No production containers started.
systemctl enable --now "$ARCHIVE_UNIT"
systemctl enable --now "$DOCKER_UNIT"

systemctl is-active --quiet "$ARCHIVE_UNIT" || {
  echo 'ERROR: archive service not active after enable' >&2
  exit 4
}
systemctl is-active --quiet "$DOCKER_UNIT" || {
  echo 'ERROR: Docker service not active after enable' >&2
  exit 4
}
DOCKER_VERSION="$(docker_api_version)" || {
  echo 'ERROR: Docker daemon API did not respond within 10 seconds' >&2
  exit 4
}
[[ -n "$DOCKER_VERSION" ]] || {
  echo 'ERROR: Docker daemon returned no server version' >&2
  exit 4
}
echo "Docker API reachable: server_version=$DOCKER_VERSION"

echo 'BASE_SERVICES_READY: Docker and external archive active.'
echo 'No GitHub runners, zCloud, training, browser or containers altered.'
echo 'The final SQLite writer fence, independent training disk and worker registration are separate gated cutovers.'

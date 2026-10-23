#!/usr/bin/env bash
# New VPS base-service activation. Never modify GitHub runner registrations,
# worker processes, SQLite, training, browser sessions, or public ingress.
# Read-only by default. --apply only enables *existing* Docker/archive units.
set -Eeuo pipefail

EXPECTED_HOST='VM-7eb5ee16-2c71-440d-b7fd-8095d204e930'
ARCHIVE_UNIT='zcloud-external-archive.service'
ARCHIVE_MOUNT='/home/ubuntu/VPS-Archief'
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

unit_enabled() {
  systemctl is-enabled "$1" 2>/dev/null || true
}

docker_api_version() {
  command -v docker >/dev/null 2>&1 || return 1
  command -v timeout >/dev/null 2>&1 || return 1
  timeout 10s docker info --format '{{.ServerVersion}}' 2>/dev/null
}

archive_mount_is_verified() {
  command -v findmnt >/dev/null 2>&1 || return 1
  findmnt -rn -o TARGET,FSTYPE,OPTIONS --target "$ARCHIVE_MOUNT" 2>/dev/null |
    awk -v expected="$ARCHIVE_MOUNT" '$1 == expected && $2 ~ /^fuse[.]rclone$/ && $3 ~ /(^|,)ro(,|$)/ { verified=1 } END { exit !verified }'
}

echo "=== NEW VPS BASE SERVICES: $MODE ==="
echo "host=$EXPECTED_HOST"
echo "archive=$(unit_load "$ARCHIVE_UNIT")/$(unit_active "$ARCHIVE_UNIT")"
echo "archive_enabled=$(unit_enabled "$ARCHIVE_UNIT")"
echo "docker=$(unit_load "$DOCKER_UNIT")/$(unit_active "$DOCKER_UNIT")"
echo "docker_enabled=$(unit_enabled "$DOCKER_UNIT")"
if DOCKER_VERSION="$(docker_api_version)" && [[ -n "$DOCKER_VERSION" ]]; then
  echo "docker_api=reachable/$DOCKER_VERSION"
else
  echo "docker_api=unavailable"
fi
if archive_mount_is_verified; then
  echo "archive_mount=verified_readonly_fuse_rclone"
else
  echo "archive_mount=not_verified"
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
command -v findmnt >/dev/null 2>&1 || {
  echo 'REFUSED: findmnt utility missing; cannot verify archive mount' >&2
  exit 3
}
command -v awk >/dev/null 2>&1 || {
  echo 'REFUSED: awk utility missing; cannot verify archive mount' >&2
  exit 3
}
command -v flock >/dev/null 2>&1 || {
  echo 'REFUSED: flock utility missing; cannot serialize base activation' >&2
  exit 3
}

# Serialize this exact host-bound activation before any service start or
# persistent enablement. --status exits above and remains entirely read-only.
exec 9>/run/lock/zcloud-new-vps-base-activation.lock
flock -n 9 || {
  echo 'REFUSED: another base activation is already running' >&2
  exit 4
}

# Refuse unusual unit states rather than changing masked/static/runtime-only
# units or guessing what their current enablement means.
ARCHIVE_ENABLED_BEFORE="$(unit_enabled "$ARCHIVE_UNIT")"
DOCKER_ENABLED_BEFORE="$(unit_enabled "$DOCKER_UNIT")"
for state in "$ARCHIVE_ENABLED_BEFORE" "$DOCKER_ENABLED_BEFORE"; do
  case "$state" in
    enabled|disabled) ;;
    *) echo "REFUSED: unexpected base-service enablement state: $state" >&2; exit 3 ;;
  esac
done

# Explicit operator action: start existing units first, verify their *actual*
# readiness, then persist enablement. A broken archive or Docker daemon must
# not be permanently enabled simply because systemd accepted the start.
# Never stop any pre-existing service or runner as an implicit rollback.
systemctl start "$ARCHIVE_UNIT" || {
  echo 'ERROR: archive service failed to start; no Docker action attempted' >&2
  exit 4
}
systemctl is-active --quiet "$ARCHIVE_UNIT" || {
  echo 'ERROR: archive service is not active; no Docker action attempted' >&2
  exit 4
}
archive_mount_is_verified || {
  echo "ERROR: $ARCHIVE_MOUNT is not a verified read-only fuse.rclone mount; Docker not started" >&2
  exit 4
}
systemctl start "$DOCKER_UNIT" || {
  echo 'ERROR: Docker service failed to start; no service enablement attempted' >&2
  exit 4
}
systemctl is-active --quiet "$DOCKER_UNIT" || {
  echo 'ERROR: Docker service not active; no service enablement attempted' >&2
  exit 4
}
DOCKER_VERSION="$(docker_api_version)" || {
  echo 'ERROR: Docker daemon API did not respond within 10 seconds; no service enablement attempted' >&2
  exit 4
}
[[ -n "$DOCKER_VERSION" ]] || {
  echo 'ERROR: Docker daemon returned no server version; no service enablement attempted' >&2
  exit 4
}
echo "Docker API reachable: server_version=$DOCKER_VERSION"

# Persist desired base-service start-on-boot only after both probes pass.
# If an enable step fails, report partial configuration rather than claiming
# ready. Do not tear down services that could already serve other owners.
if [[ "$ARCHIVE_ENABLED_BEFORE" == disabled ]]; then
  systemctl enable "$ARCHIVE_UNIT" || {
    echo 'ERROR: archive unit enable failed; running services have not been stopped' >&2
    exit 4
  }
fi
[[ "$(unit_enabled "$ARCHIVE_UNIT")" == enabled ]] || {
  echo 'ERROR: archive unit enablement read-back did not confirm persistent enabled state' >&2
  exit 4
}
if [[ "$DOCKER_ENABLED_BEFORE" == disabled ]]; then
  systemctl enable "$DOCKER_UNIT" || {
    echo 'ERROR: Docker unit enable failed; archive may already be enabled; manual reconciliation required' >&2
    exit 4
  }
fi
[[ "$(unit_enabled "$DOCKER_UNIT")" == enabled ]] || {
  echo 'ERROR: Docker unit enablement read-back did not confirm persistent enabled state' >&2
  exit 4
}
echo 'BASE_SERVICES_READY: Docker API responsive, read-only archive verified, both units persistently enabled and read back.'
echo 'No GitHub runners, zCloud, training, browser or containers altered.'
echo 'The final SQLite writer fence, independent training disk and worker registration are separate gated cutovers.'

#!/usr/bin/env bash
# Safe new-VPS activation: runs only on the intended server, preserves old runner identities.
set -Eeuo pipefail
EXPECTED=VM-7eb5ee16-2c71-440d-b7fd-8095d204e930
[[ $(hostname) == "$EXPECTED" && $EUID -eq 0 ]] || { echo 'REFUSED: run as root on the NEW VPS only'; exit 2; }
LOG=/var/log/zcloud-new-vps-activation.log
exec > >(tee -a "$LOG") 2>&1
echo "START $(date -Is)"
# The primary goal is to restore base services without two machines claiming the same runner credentials.
systemctl daemon-reload
if systemctl list-unit-files zcloud-external-archive.service --no-legend | grep -q zcloud-external-archive; then
  systemctl enable --now zcloud-external-archive.service
fi
systemctl is-active --quiet zcloud-external-archive.service || { echo 'ERROR: external archive is not active'; exit 3; }
# Never start migrated runner units with old registrations/names.
mapfile -t RUNNERS < <(systemctl list-unit-files --no-legend 'actions.runner.*.service' | awk '{print $1}')
for unit in "${RUNNERS[@]}"; do
  systemctl disable "$unit" >/dev/null 2>&1 || true
  if systemctl is-active --quiet "$unit"; then
    echo "Stopping duplicate-prone cloned runner unit: $unit"
    systemctl stop "$unit"
  fi
done
echo "Migrated GitHub runners remain off: ${#RUNNERS[@]}"
if ! command -v docker >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y docker.io
fi
systemctl enable --now docker
docker info >/dev/null
echo 'Docker engine active; deliberately not starting migrated production containers.'
# The browser supervisor is a static unit; inspect dependencies before any worker cutover.
for unit in zcloud-firefox-supervisor.service zcloud-self-heal.service zennay-portfolio-runner-pool.service; do
  systemctl cat "$unit" 2>/dev/null | sed -n '1,35p' || true
done
printf '\n=== EXTERNAL STORAGE ===\n'
findmnt -rn -o TARGET,SOURCE,FSTYPE | grep -E 'VPS-Archief|rclone' || true
printf '\n=== LOCAL RESTORE PRESENCE ===\n'
for path in /home/ubuntu/zennay-cloud /home/ubuntu/.mozilla/firefox /home/ubuntu/actions-runner-zcloud; do
  [[ -e "$path" ]] && echo "PRESENT $path" || echo "MISSING $path"
done
printf '\n=== SERVICES ===\n'
systemctl is-active docker zcloud-external-archive.service xrdp || true
printf '\n=== FINISHED $(date -Is) ===\n'
echo 'Next cutover: register fresh runner identities, switch zSSH endpoint and activate worker supervisors only after old runner shutdown.'

#!/usr/bin/env bash
# Run as root on the new VPS. Collects safe, non-secret cutover evidence and starts existing base services.
set -Eeuo pipefail
expected='VM-7eb5ee16-2c71-440d-b7fd-8095d204e930'
[[ "$EUID" -eq 0 && "$(hostname)" == "$expected" ]] || { echo "Refusing: this is not root on $expected"; exit 2; }
out="/var/log/zcloud-cutover-$(date +%Y%m%d-%H%M%S).log"
exec > >(tee "$out") 2>&1
echo "=== $(date -Is) / $(hostname) ==="
echo "-- storage --"
systemctl enable --now zcloud-external-archive.service || true
systemctl is-active zcloud-external-archive.service || true
df -h / || true
findmnt -rn -o TARGET,SOURCE,FSTYPE | grep -E 'VPS-Archief|/dev/sda' || true
echo "-- docker --"
if command -v docker >/dev/null 2>&1; then
 systemctl enable --now docker
 docker info --format 'Server: {{.ServerVersion}}' || true
 docker ps -a --format '{{.Names}}: {{.Status}}' || true
else
 echo 'Docker not installed. Use the activation script first.'
fi
echo "-- files --"
for p in /home/ubuntu/.mozilla/firefox/profiles.ini /home/ubuntu/zennay-cloud /home/ubuntu/actions-runner-zcloud /home/desktop/.xsession /home/ubuntu/.config/zssh /home/ubuntu/.local/bin/zssh; do
 [[ -e "$p" ]] && echo "PRESENT $p" || echo "MISSING $p"
done
echo "-- units --"
systemctl list-unit-files --no-legend | grep -Ei '^(zssh|zcloud|actions\.runner)' | sed -E 's/[[:space:]]+/ /g' || true
echo "-- zssh socket --"
ss -lnt | grep -E ':8788|:8789' || true
echo "-- migrated runner unit safety --"
mapfile -t old_units < <(systemctl list-unit-files --no-legend 'actions.runner.*.service' | awk '{print $1}')
for unit in "${old_units[@]}"; do
  case "$unit" in
   *vps-bb300bba*|*zcloud-vps-1*|*zssh-vps-1*)
    systemctl disable "$unit" >/dev/null 2>&1 || true
    systemctl stop "$unit" >/dev/null 2>&1 || true
    echo "Disabled cloned runner identity: $unit";;
  esac
done
echo "-- loaded service state --"
systemctl is-active docker xrdp zcloud-external-archive.service zssh.service 2>/dev/null || true
echo "DONE log=$out"

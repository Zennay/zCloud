#!/usr/bin/env bash
# Read-only NEW-VPS cutover inventory by default.
# --apply requires root and may start base services/disable INACTIVE cloned runner units.
# It never stops an active runner and does not start zCloud workers or training.
set -Eeuo pipefail

expected='VM-7eb5ee16-2c71-440d-b7fd-8095d204e930'
mode="${1:-status}"
[[ "$(hostname)" == "$expected" ]] || {
  echo "Refusing: wrong host; expected $expected" >&2
  exit 2
}
[[ "$mode" == status || "$mode" == --apply ]] || {
  echo 'Usage: new-vps-cutover-inventory-20261022.sh [status|--apply]' >&2
  exit 2
}

apply=0
if [[ "$mode" == --apply ]]; then
  [[ "$EUID" -eq 0 ]] || {
    echo 'Refusing: --apply requires root on the new VPS' >&2
    exit 2
  }
  apply=1
fi

# Collect cloned runner units and their state BEFORE any possible service mutation.
# A copied identity must never be stopped while its listener or job may be active.
mapfile -t old_units < <(
  systemctl list-unit-files --no-legend 'actions.runner.*.service' 2>/dev/null |
    awk '{print $1}' |
    grep -E 'vps-bb300bba|zcloud-vps-1|zssh-vps-1' || true
)
echo "-- cloned runner identity preflight --"
for unit in "${old_units[@]}"; do
  state="$(systemctl show "$unit" --property=ActiveState --value 2>/dev/null || true)"
  echo "CLONED $unit ActiveState=${state:-unknown}"
  if ((apply)); then
    case "$state" in
      inactive|failed) ;;
      *)
        echo "Refusing --apply: $unit is ${state:-unknown}; drain and inspect it manually first" >&2
        exit 2
        ;;
    esac
  fi
done

if ((apply)); then
  out="/var/log/zcloud-cutover-$(date +%Y%m%d-%H%M%S).log"
  exec > >(tee "$out") 2>&1
  echo "=== APPLY $(date -Is) / $(hostname) ==="
else
  echo "=== READ ONLY $(date -Is) / $(hostname) ==="
fi

echo "-- storage --"
if ((apply)); then
  systemctl enable --now zcloud-external-archive.service
fi
systemctl is-active zcloud-external-archive.service 2>/dev/null || true
df -hT / || true
findmnt -rn -o TARGET,SOURCE,FSTYPE |
  grep -E 'VPS-Archief|/dev/sd|/dev/nvme|rclone|fuse' || true
echo 'Training storage is NOT approved by an archive mount or root disk listing.'

echo "-- docker --"
if command -v docker >/dev/null 2>&1; then
  if ((apply)); then
    systemctl enable --now docker
  fi
  docker info --format 'Server: {{.ServerVersion}}' 2>/dev/null || true
  docker ps -a --format '{{.Names}}: {{.Status}}' 2>/dev/null || true
else
  echo 'Docker not installed. Use the separate operator-approved activation path.'
fi

echo "-- files --"
for p in /home/ubuntu/.mozilla/firefox/profiles.ini /home/ubuntu/zennay-cloud /home/ubuntu/actions-runner-zcloud /home/desktop/.xsession /home/ubuntu/.config/zssh /home/ubuntu/.local/bin/zssh; do
  [[ -e "$p" ]] && echo "PRESENT $p" || echo "MISSING $p"
done

echo "-- units --"
systemctl list-unit-files --no-legend 2>/dev/null |
  grep -Ei '^(zssh|zcloud|actions\.runner)' |
  sed -E 's/[[:space:]]+/ /g' || true

echo "-- zssh socket --"
ss -lnt 2>/dev/null | grep -E ':8788|:8789' || true

if ((apply)); then
  echo '-- disabling only INACTIVE cloned runner units --'
  for unit in "${old_units[@]}"; do
    # Re-check after other startup actions in case another operator changed state.
    state="$(systemctl show "$unit" --property=ActiveState --value 2>/dev/null || true)"
    case "$state" in
      inactive|failed) systemctl disable "$unit" ;;
      *)
        echo "Refusing to disable changed runner $unit: ${state:-unknown}" >&2
        exit 2
        ;;
    esac
  done
fi

echo "-- loaded service state --"
systemctl is-active docker xrdp zcloud-external-archive.service zssh.service 2>/dev/null || true
echo "DONE mode=$mode"

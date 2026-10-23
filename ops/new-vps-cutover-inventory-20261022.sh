#!/usr/bin/env bash
# New-VPS cutover inventory. Read-only by default; opt in to base-service startup.
# Never stops migrated runner units or touches SQLite/Firefox/worker state.
set -Eeuo pipefail

expected='VM-7eb5ee16-2c71-440d-b7fd-8095d204e930'
[[ $# -le 1 ]] || {
  echo "Refusing extra arguments; use --help for the supported modes." >&2
  exit 2
}
mode='status'
case "${1:-}" in
  '') ;;
  --apply-base) mode='apply-base' ;;
  --help)
    printf 'Usage: %s [--apply-base]\n' "$0"
    printf 'Default: read-only inventory. --apply-base starts Docker and the archive mount unit only.\n'
    exit 0
    ;;
  *) echo "Refusing unknown option: $1" >&2; exit 2 ;;
esac
[[ "$EUID" -eq 0 && "$(hostname)" == "$expected" ]] || {
  echo "Refusing: this command must run as root on $expected" >&2
  exit 2
}
if [[ "$mode" == 'apply-base' ]]; then
  # Validate every prerequisite before the first state change to avoid partial activation.
  archive_load="$(systemctl show zcloud-external-archive.service -p LoadState --value 2>/dev/null || true)"
  docker_load="$(systemctl show docker.service -p LoadState --value 2>/dev/null || true)"
  [[ "$archive_load" == 'loaded' ]] || {
    echo 'Refusing base activation: zcloud-external-archive.service is not installed.' >&2
    exit 3
  }
  [[ "$docker_load" == 'loaded' ]] || {
    echo 'Refusing base activation: docker.service is not installed.' >&2
    exit 3
  }
  command -v docker >/dev/null 2>&1 || {
    echo 'Refusing base activation: Docker CLI is missing; no package install will be attempted.' >&2
    exit 3
  }
  # Deliberately narrow opt-in: no GitHub runner, scheduler, queue or browser mutation.
  out="/var/log/zcloud-cutover-base-$(date +%Y%m%d-%H%M%S).log"
  exec > >(tee "$out") 2>&1
  echo "-- explicit base-service activation --"
  echo "preflight archive=$archive_load docker=$docker_load docker_cli=present"
  systemctl enable --now zcloud-external-archive.service
  systemctl enable --now docker.service
fi

echo "=== $(date -Is) / $(hostname) / mode=$mode ==="
echo "-- storage; filesystem type and source must be inspected before any training --"
df -hT /
findmnt -rn -o TARGET,SOURCE,FSTYPE |
  grep -E 'VPS-Archief|/dev/|fuckball|haxlab' || true
echo "-- external training guard --"
echo 'A rclone/FUSE archive or /dev/sda2 system partition is NEVER a training disk.'
echo 'Fuckball external training requires a separately mounted ext4/xfs/btrfs block disk.'
echo "-- docker --"
if command -v docker >/dev/null 2>&1; then
  systemctl is-active docker.service || true
  if [[ "$mode" == 'apply-base' ]]; then
    docker info --format 'Server: {{.ServerVersion}}' || true
  fi
  docker ps -a --format '{{.Names}}: {{.Status}}' || true
else
  echo 'Docker not installed.'
fi
echo "-- file presence only (no secret contents) --"
for p in \
  /home/ubuntu/.mozilla/firefox/profiles.ini \
  /home/ubuntu/zennay-cloud \
  /home/ubuntu/actions-runner-zcloud \
  /home/desktop/.xsession \
  /home/ubuntu/.config/zssh \
  /home/ubuntu/.local/bin/zssh; do
  [[ -e "$p" ]] && echo "PRESENT $p" || echo "MISSING $p"
done
echo "-- discovered unit names --"
systemctl list-unit-files --no-legend |
  grep -Ei '^(zssh|zcloud|actions\.runner)' |
  sed -E 's/[[:space:]]+/ /g' || true
echo "-- unit activity, no starts/stops --"
systemctl is-active docker.service xrdp.service zcloud-external-archive.service zssh.service 2>/dev/null || true
echo "-- zssh listen sockets --"
ss -lnt | grep -E ':8788|:8789' || true
echo "-- runner identities: report only; retirement requires separate drain and registration proof --"
systemctl list-unit-files --no-legend 'actions.runner.*.service' |
  sed -E 's/[[:space:]]+/ /g' || true
echo "DONE mode=$mode"

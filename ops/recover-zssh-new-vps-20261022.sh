#!/usr/bin/env bash
# New VPS zSSH recovery: inspect first, then start only an existing configured unit.
set -Eeuo pipefail
EXPECTED="VM-7eb5ee16-2c71-440d-b7fd-8095d204e930"
[[ $(hostname) == "$EXPECTED" && $EUID -eq 0 ]] || { echo "REFUSED: new VPS root only"; exit 2; }
LOG=/var/log/zcloud-zssh-new-vps-recovery.log
exec > >(tee -a "$LOG") 2>&1
echo "=== RECOVERY $(date -Is) ==="
echo "-- zSSH unit discovery --"
systemctl list-unit-files --all --no-legend | grep -E '^zssh(-[^ ]+)?\.service' || true
echo "-- zSSH executable discovery --"
for p in /home/ubuntu/.local/bin/zssh /usr/local/bin/zssh /opt/zssh /home/ubuntu/zSSH /home/ubuntu/zennay-cloud/zSSH; do
 [[ -e "$p" ]] && ls -ld "$p"
done
echo "-- unit runtime configuration --"
for u in zssh.service zssh-private.service; do
 if systemctl cat "$u" >/dev/null 2>&1; then
  systemctl show "$u" -p User -p ExecStart -p FragmentPath -p LoadState -p ActiveState
  echo "Found configured service: $u"
  # Avoid exposing environment credentials; no systemctl cat output.
  systemctl enable --now "$u"
  systemctl is-active "$u"
 fi
done
echo "-- loopback listeners (do not publish publicly) --"
ss -lnt | awk 'NR==1 || /:8788|:8789/'
echo "-- identity --"
hostname
echo "Recovery done. Keep the existing ChatGPT connector pointing to OLD VPS until its endpoint is deliberately updated."

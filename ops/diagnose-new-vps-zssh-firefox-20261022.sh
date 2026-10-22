#!/usr/bin/env bash
# Read-only diagnosis for the NEW VPS. No secrets displayed, no services restarted.
set -Eeuo pipefail
EXPECTED=VM-7eb5ee16-2c71-440d-b7fd-8095d204e930
[[ "$(hostname)" == "$EXPECTED" ]] || { echo "WRONG HOST: $(hostname)"; exit 2; }
echo "=== zSSH ownership and actual service ==="
ss -lntp '( sport = :8788 )' 2>/dev/null || true
for u in ubuntu zssh; do
 id "$u" >/dev/null 2>&1 || continue
 uid=$(id -u "$u")
 echo "User: $u, uid: $uid"
 systemctl --user -M "$u@" show zssh.service -p LoadState -p ActiveState -p FragmentPath --no-pager 2>/dev/null || true
 if [[ -d "/home/$u/.config/systemd/user" ]]; then
  find "/home/$u/.config/systemd/user" -maxdepth 1 -name '*zssh*.service' -printf '%f\n' 2>/dev/null || true
 fi
done
echo "=== Browser profile location (names only) ==="
for dir in /home/ubuntu/.mozilla/firefox /home/ubuntu/snap/firefox/common/.mozilla/firefox /home/desktop/.mozilla/firefox /home/desktop/snap/firefox/common/.mozilla/firefox; do
 if [[ -d "$dir" ]]; then
  echo "PRESENT $dir"
  find "$dir" -maxdepth 2 \( -name profiles.ini -o -name installs.ini -o -name 'extensions.json' -o -name '*.default*' \) -printf '%P\n' 2>/dev/null | head -25
 fi
done
echo "=== Former browser Firefox locations in migration stage ==="
find /home/vps-migrate/old-vps-stage -maxdepth 5 \( -name profiles.ini -o -name installs.ini \) -print 2>/dev/null | head -15
echo "=== User services, timers, browser processes ==="
systemctl list-timers --all --no-pager | grep -Ei 'firefox|zcloud|portfolio|runner' || true
ps -eo user,comm,args | grep -Ei '[f]irefox|[z]ssh|[p]lasmashell' | cut -c1-150 | head -30
echo 'COMPLETED: no changes performed.'

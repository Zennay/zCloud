#!/usr/bin/env bash
# New-VPS zCloud control plane + browser-worker bootstrap.
# Deliberately separates installing files from taking over an active fleet.
set -Eeuo pipefail
EXPECTED_HOST="VM-7eb5ee16-2c71-440d-b7fd-8095d204e930"
ZUSER=ubuntu
ZHOME=/home/ubuntu
ROOT="$ZHOME/zennay-cloud"
SOURCE_DB="$ZHOME/zcloud-history-migration.db"
TARGET_DB="$ROOT/history.db"
USER_UNIT="$ZHOME/.config/systemd/user/chatgpt-firefox.service"
SYSTEM_UNIT=/etc/systemd/system/zennay-cloud.service
ACTION="${1:-prepare}"

die() { printf 'REFUSED: %s\n' "$*" >&2; exit 2; }
msg() { printf '\n=== %s ===\n' "$*"; }
[[ "$EUID" -eq 0 ]] || die "run this as root on the new VPS"
[[ "$(hostname)" == "$EXPECTED_HOST" ]] || die "wrong host: $(hostname)"
[[ "$ACTION" == prepare || "$ACTION" == activate || "$ACTION" == status ]] ||
  die "usage: bash ops/new-vps-worker-bootstrap-20261022.sh [prepare|status|activate]"
[[ -f "$ROOT/server.py" && -f "$ROOT/scripts/apply_worker_fleet.py" ]] ||
  die "expected zCloud checkout missing at $ROOT"
[[ ! -L "$ROOT" && ! -L "$TARGET_DB" ]] || die "symlinked checkout/database refused"
id "$ZUSER" >/dev/null || die "ubuntu user is missing"

status() {
  msg "Database files"
  for file in "$SOURCE_DB" "$TARGET_DB"; do
    if [[ -f "$file" ]]; then stat -c '%n | %s bytes | %y' "$file"; else echo "MISSING $file"; fi
  done
  msg "Existing services (do not conflate an installed unit with active workers)"
  systemctl is-active zennay-cloud.service 2>/dev/null || true
  systemctl --user -M ubuntu@ is-active chatgpt-firefox.service 2>/dev/null || true
  echo "Runner registration state (enabled does not mean running):"
  while read -r unit _; do
    [[ -n "$unit" ]] || continue
    state="$(systemctl show "$unit" --property=ActiveState --value 2>/dev/null || true)"
    case "$unit" in
      *vps-bb300bba*|*zcloud-vps-1*|*zssh-vps-1*) origin="OLD-IDENTITY";;
      *) origin="NEW-IDENTITY";;
    esac
    printf '%s %s %s\n' "$origin" "${state:-unknown}" "$unit"
  done < <(systemctl list-unit-files --no-legend 'actions.runner.*.service' 2>/dev/null || true)
  msg "Browser prerequisites"
  for path in "$ZHOME/.Xauthority" "$ZHOME/snap/firefox/common/chatgpt-webext-profile" \
    "$ZHOME/.local/bin/web-ext" "$ROOT/firefox-extension"; do
    [[ -e "$path" ]] && echo "PRESENT $path" || echo "MISSING $path"
  done
  msg "Local API (HTTP codes only)"
  curl --noproxy '*' --max-time 3 -s -o /dev/null -w '/api/status HTTP %{http_code}\n' \
    http://127.0.0.1:8765/api/status || true
  curl --noproxy '*' --max-time 3 -s -o /dev/null -w '/api/runner-live HTTP %{http_code}\n' \
    http://127.0.0.1:8765/api/runner-live || true
}

if [[ "$ACTION" == status ]]; then
  status
  exit 0
fi

if [[ "$ACTION" == prepare ]]; then
  msg "Install minimal worker launch dependencies"
  missing=()
  for spec in python3:python3 rsync:rsync curl:curl xdotool:xdotool; do
    bin="${spec%%:*}"; package="${spec#*:}"
    command -v "$bin" >/dev/null 2>&1 || missing+=("$package")
  done
  if ((${#missing[@]} > 0)); then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y "${missing[@]}"
  fi

  msg "Stage historical SQLite snapshot without overwriting new state"
  [[ -f "$SOURCE_DB" ]] || die "transferred database missing: $SOURCE_DB"
  if [[ -e "$TARGET_DB" ]]; then
    echo "EXISTING $TARGET_DB — untouched; reconcile the two databases before activation."
  else
    python3 - "$SOURCE_DB" "$TARGET_DB" <<'PY'
import os, pwd, sqlite3, sys, tempfile
from pathlib import Path
src, dst = map(Path, sys.argv[1:])
fd, tmp = tempfile.mkstemp(prefix='.history-migration-', suffix='.db', dir=dst.parent)
os.close(fd)
try:
    with sqlite3.connect(f"file:{src}?mode=ro", uri=True) as source:
        with sqlite3.connect(tmp) as target:
            source.backup(target, pages=1000, sleep=0.1)
    with sqlite3.connect(f"file:{tmp}?mode=ro", uri=True) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'portfolio_queue', 'runner_workers', 'runner_targets'} <= tables:
            raise RuntimeError(f"historical database lacks worker tables: {sorted(tables)}")
    identity = pwd.getpwnam("ubuntu")
    os.chown(tmp, identity.pw_uid, identity.pw_gid)
    os.chmod(tmp, 0o600)
    os.replace(tmp, dst)
    print("STAGED", dst, dst.stat().st_size, "bytes")
finally:
    if os.path.exists(tmp):
        os.unlink(tmp)
PY
    chown "$ZUSER:$ZUSER" "$TARGET_DB"
  fi

  msg "Install service definitions but keep them OFF"
  install -d -o "$ZUSER" -g "$ZUSER" -m 0750 "$ZHOME/.local" "$ZHOME/.local/bin" \
    "$ZHOME/.config" "$ZHOME/.config/systemd" "$ZHOME/.config/systemd/user"
  for name in chatgpt-firefox-prestart.sh; do
    target="$ZHOME/.local/bin/$name"
    if [[ ! -e "$target" ]]; then
      install -o "$ZUSER" -g "$ZUSER" -m 0750 "$ROOT/deploy/$name" "$target"
    else
      echo "KEEP existing $target"
    fi
  done
  target="$ZHOME/.local/bin/chatgpt-window-layout.sh"
  if [[ ! -e "$target" ]]; then
    install -o "$ZUSER" -g "$ZUSER" -m 0750 "$ROOT/local-bin/chatgpt-window-layout.sh" "$target"
  else
    echo "KEEP existing $target"
  fi
  if [[ ! -e "$USER_UNIT" ]]; then
    install -o "$ZUSER" -g "$ZUSER" -m 0644 "$ROOT/deploy/chatgpt-firefox.service" "$USER_UNIT"
    echo "INSTALLED inactive user service $USER_UNIT"
  else
    echo "KEEP existing $USER_UNIT"
  fi
  if systemctl --user -M ubuntu@ cat zennay-cloud.service >/dev/null 2>&1; then
    echo "PREEXISTING ubuntu user zennay-cloud.service: not creating a competing system unit."
  elif ! systemctl cat zennay-cloud.service >/dev/null 2>&1; then
    cat > "$SYSTEM_UNIT" <<'UNIT'
[Unit]
Description=zCloud control-plane API (new VPS)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=/home/ubuntu/zennay-cloud
Environment=PYTHONUNBUFFERED=1
Environment=ZENNAY_BIND=127.0.0.1
Environment=ZENNAY_PORT=8765
ExecStart=/usr/bin/python3 /home/ubuntu/zennay-cloud/server.py
Restart=on-failure
RestartSec=10
UMask=0077
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
UNIT
    chmod 0644 "$SYSTEM_UNIT"
    echo "INSTALLED inactive system service $SYSTEM_UNIT"
  else
    echo "KEEP existing zennay-cloud.service (no overwrite)"
  fi
  msg "Stage existing self-heal recovery unit and timer (do NOT activate)"
  # Keep the historical self-heal disabled during migration. Its timer will
  # only be armed after the authenticated fleet reports ten fresh heartbeats.
  for unit in zcloud-self-heal.service zcloud-self-heal.timer; do
    if [[ ! -e "/etc/systemd/system/$unit" ]]; then
      install -o root -g root -m 0644 "$ROOT/deploy/$unit" "/etc/systemd/system/$unit"
      echo "STAGED inactive $unit"
    else
      echo "KEEP existing $unit"
    fi
  done
  if [[ ! -e /usr/local/sbin/zcloud-self-heal ]]; then
    install -o root -g root -m 0755 "$ROOT/scripts/zcloud-self-heal.sh" /usr/local/sbin/zcloud-self-heal
    echo "STAGED self-heal executable"
  else
    echo "KEEP existing self-heal executable"
  fi
  # New host uses UID 1003, not the previous VPS's historical UID 1000.
  # Recovery must NEVER implicitly re-enable a copied Actions runner or
  # unrelated project services before their independent cutover.
  install -d -o root -g root -m 0755 /etc/systemd/system/zcloud-self-heal.service.d
  override=/etc/systemd/system/zcloud-self-heal.service.d/20-new-vps-quarantine.conf
  if [[ ! -e "$override" ]]; then
    cat > "$override" <<OVERRIDE
[Service]
Environment=ZCLOUD_RUNTIME_USER=ubuntu
Environment=ZCLOUD_RUNTIME_USER_UID=$(id -u ubuntu)
Environment=ZCLOUD_HEAL_ACTIONS_RUNNER=false
Environment=ZCLOUD_HEAL_PROJECT_RUNTIMES=false
OVERRIDE
    chmod 0644 "$override"
    echo "STAGED new-VPS self-heal quarantine drop-in"
  else
    echo "KEEP existing self-heal override $override"
  fi
  systemctl daemon-reload
  loginctl enable-linger "$ZUSER"
  systemctl --user -M ubuntu@ daemon-reload ||
    echo "WARN: ubuntu user manager not running; unit will load after login."
  msg "PREPARED; no worker, old runner identity or zCloud service started"
  echo "Before cutover: stop old VPS scheduler/workers and take a FINAL SQLite-consistent delta snapshot."
  echo "Then inspect: bash $ROOT/ops/new-vps-worker-bootstrap-20261022.sh status"
  status
  exit 0
fi

# This phase has explicit authorization boundaries. The file copy is NOT a
# live cutover; a stale history snapshot can omit today's work/leases.
[[ "${ZCLOUD_OLD_VPS_FENCED:-}" == yes ]] ||
  die "old VPS workers/scheduler must first be stopped; set ZCLOUD_OLD_VPS_FENCED=yes after verifying"
[[ "${ZCLOUD_FINAL_DB_SYNCED:-}" == yes ]] ||
  die "reconcile/final-sync the DB first; set ZCLOUD_FINAL_DB_SYNCED=yes only after verifying"
[[ -f "$TARGET_DB" ]] || die "target history.db missing; run prepare first"
[[ -f "$USER_UNIT" ]] || die "worker service not installed; run prepare first"
systemctl cat zennay-cloud.service >/dev/null 2>&1 ||
  die "system zennay-cloud.service not installed; reconcile any existing ubuntu user unit first"
while read -r unit _; do
  [[ -n "$unit" ]] || continue
  case "$unit" in
    *vps-bb300bba*|*zcloud-vps-1*|*zssh-vps-1*)
      state="$(systemctl show "$unit" --property=ActiveState --value 2>/dev/null || true)"
      [[ "$state" != active && "$state" != activating && "$state" != reloading ]] ||
        die "cloned old-VPS GitHub runner is live: $unit"
      ;;
  esac
done < <(systemctl list-unit-files --no-legend 'actions.runner.*.service' 2>/dev/null || true)
# Fresh vm-7eb5ee16 GitHub runner identities are independent and may keep working.
for scope in system user; do
  if [[ "$scope" == system ]]; then
    state="$(systemctl show zennay-cloud.service -p ActiveState --value 2>/dev/null || true)"
  else
    state="$(systemctl --user -M ubuntu@ show zennay-cloud.service -p ActiveState --value 2>/dev/null || true)"
  fi
  [[ "$state" != active && "$state" != activating && "$state" != reloading ]] ||
    die "zCloud $scope service already $state; refuse competing scheduler"
done
firefox_state="$(systemctl --user -M ubuntu@ show chatgpt-firefox.service -p ActiveState --value 2>/dev/null || true)"
[[ "$firefox_state" != active && "$firefox_state" != activating && "$firefox_state" != reloading ]] ||
  die "Firefox worker service already $firefox_state; inspect without launching duplicate sessions"
msg "Validate desired fleet against copied state (no fabricated workers)"
runuser -u "$ZUSER" -- python3 "$ROOT/scripts/apply_worker_fleet.py"
msg "Apply fleet desired state while API is stopped"
runuser -u "$ZUSER" -- python3 "$ROOT/scripts/apply_worker_fleet.py" --apply
msg "Start local-only zCloud control plane"
systemctl enable --now zennay-cloud.service
systemctl is-active --quiet zennay-cloud.service || die "API failed to start"

# Existing Firefox extension is not itself an authenticated browser worker.
# Never launch an empty/mismatched browser profile, or seize someone else's X display.
if [[ -f "$ZHOME/.Xauthority" && -d "$ZHOME/snap/firefox/common/chatgpt-webext-profile" \
      && -d "$ROOT/firefox-extension" && -x "$ZHOME/.local/bin/web-ext" ]] &&
   runuser -u "$ZUSER" -- env DISPLAY=:10 XAUTHORITY="$ZHOME/.Xauthority" \
     xdotool getdisplaygeometry >/dev/null 2>&1; then
  msg "Start existing Firefox profile/extension worker service"
  systemctl --user -M ubuntu@ enable --now chatgpt-firefox.service ||
    echo "WARN: browser service could not start; inspect journalctl --user -M ubuntu@"
else
  echo "BROWSER NOT STARTED: migrated authenticated Firefox profile, web-ext or ubuntu :10 display missing."
  echo "Restore/verify the original profile and X session first; never fabricate active sessions."
fi
msg "GitHub Actions runners"
echo "Not registered: new VPS requires FRESH GitHub repo-scoped runner credentials and a unique runner name."
echo "Never enable cloned actions.runner.* units from the old VPS."
status
echo "CUTOVER ATTEMPTED: worker heartbeat /api/runner-live must be verified before calling this live."

#!/usr/bin/env bash
# Install a bounded, auditable, fail-closed retention timer on the NEW VPS.
set -Eeuo pipefail
EXPECTED_HOST=VM-7eb5ee16-2c71-440d-b7fd-8095d204e930
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
[[ "${EUID}" -eq 0 && "$(hostname)" == "$EXPECTED_HOST" ]] || {
  echo "REFUSED: only root on the exact new VPS can install this service" >&2
  exit 2
}
command -v python3 >/dev/null && command -v systemctl >/dev/null
[[ -x /home/ubuntu/.local/bin/rclone ]] || { echo "REFUSED: ubuntu rclone is missing" >&2; exit 3; }
[[ -f /home/ubuntu/.config/rclone/rclone.conf ]] || { echo "REFUSED: ubuntu rclone config missing" >&2; exit 3; }
python3 -m json.tool "$SCRIPT_DIR/archive-retention.example.json" >/dev/null
POLICY=/etc/zcloud-storage/retention.json
install -d -m 0755 /etc/zcloud-storage /usr/local/libexec /etc/systemd/system/zcloud-cold-archive.service.d
if [[ ! -e "$POLICY" ]]; then
  install -m 0644 "$SCRIPT_DIR/archive-retention.example.json" "$POLICY"
  echo "Installed initial conservative cold-directory policy. Existing policy is never overwritten."
fi
python3 -m json.tool "$POLICY" >/dev/null
# Do not grant systemd blanket write access: only explicit cold directories.
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
python3 - "$POLICY" > "$tmp" <<'PY'
import json, pathlib, sys
p=json.load(open(sys.argv[1]))
print("[Service]")
paths=set()
for project in p["projects"]:
    root=pathlib.Path(project["root"])
    if not root.is_absolute() or root.is_symlink() or ".." in root.parts:
        raise SystemExit("Unsafe project root")
    for sub in project.get("cold_directories",[]):
        if not isinstance(sub,str) or not sub or sub.startswith("/"):
            raise SystemExit("Unsafe cold directory")
        rel=pathlib.Path(sub)
        if not rel.parts or any(part in (".","..") for part in rel.parts):
            raise SystemExit("Unsafe cold directory")
        full=root/rel
        if not any(full.is_relative_to(pathlib.Path(base)) for base in
                   ("/opt","/srv","/var/lib","/home/ubuntu")):
            raise SystemExit("Not in approved application roots")
        paths.add(str(full))
for path in sorted(paths):
    # Prefix '-' makes not-yet-existing cold directories optional.
    print(f"ReadWritePaths=-{path}")
PY
# Verify remote access as the exact low-privilege service user before installation.
runuser -u ubuntu -- /home/ubuntu/.local/bin/rclone lsf \
    vpsarchive:zcloud-vps/automatic --max-depth 1 >/dev/null || {
  echo "REFUSED: encrypted archive remote unavailable; timer stays disabled" >&2
  exit 4
}
install -m 0755 "$SCRIPT_DIR/archive-retention.py" /usr/local/libexec/zcloud-cold-archive.py
install -m 0644 "$SCRIPT_DIR/zcloud-cold-archive.service" /etc/systemd/system/zcloud-cold-archive.service
install -m 0644 "$SCRIPT_DIR/zcloud-cold-archive.timer" /etc/systemd/system/zcloud-cold-archive.timer
install -m 0644 "$tmp" /etc/systemd/system/zcloud-cold-archive.service.d/10-cold-writable.conf
# Read-only candidate discovery before enabling the scheduled apply.
runuser -u ubuntu -- /usr/bin/python3 /usr/local/libexec/zcloud-cold-archive.py \
    --config "$POLICY" --state-dir /tmp/zcloud-cold-archive-install-dry-run || {
  echo "REFUSED: dry-run failed; timer not changed" >&2
  exit 5
}
systemctl daemon-reload
systemctl enable --now zcloud-cold-archive.timer
echo "INSTALLED TIMER: $(systemctl is-active zcloud-cold-archive.timer)"
systemctl list-timers zcloud-cold-archive.timer --no-pager
echo "First apply (optional now): systemctl start zcloud-cold-archive.service"

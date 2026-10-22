#!/usr/bin/env bash
# Fail-closed external-only trainer. Designated new VPS ONLY.
set -Eeuo pipefail
EXPECTED_HOST='VM-7eb5ee16-2c71-440d-b7fd-8095d204e930'
REPO='https://github.com/Zennay/fuckball.git'
UNIT='fuckball-ai-league.service'
if [[ "$(hostname)" != "$EXPECTED_HOST" || "$EUID" == 0 ]]; then
  echo "REFUSED: requires an unprivileged runner on $EXPECTED_HOST (actual $(hostname))" >&2
  exit 20
fi
for command_name in findmnt lsblk readlink stat git node python3 sudo mountpoint systemctl; do
  command -v "$command_name" >/dev/null || { echo "Missing $command_name" >&2; exit 21; }
done
sudo -n true || { echo 'No scoped noninteractive sudo'; exit 22; }

system_source="$(readlink -f "$(findmnt -n -o SOURCE -T /)")"
system_disk="$(lsblk -nrpo PKNAME "$system_source" 2>/dev/null | head -n1 || true)"
[[ -n "$system_disk" ]] || system_disk="$system_source"
# An rclone/FUSE archive is not a safe training disk: its VFS cache can fill /.
# Only deliberately named, separately mounted, block-backed volumes qualify.
set -- /mnt/external /mnt/storage /mnt/data /mnt/training /srv/external /srv/storage /data /media/storage /mnt/VPS-Archief
if [[ -n "$FUCKBALL_EXTERNAL_MOUNT" ]]; then set -- "$FUCKBALL_EXTERNAL_MOUNT"; fi
MOUNT=''
for candidate in "$@"; do
  [[ -d "$candidate" && ! -L "$candidate" ]] || continue
  [[ "$(findmnt -n -o TARGET -T "$candidate")" == "$candidate" && "$candidate" != / ]] || continue
  fstype="$(findmnt -n -o FSTYPE -T "$candidate")"
  case "$fstype" in ext4|xfs|btrfs) ;; *) echo "Skip $candidate ($fstype is unsafe/unsupported)"; continue;; esac
  dev="$(findmnt -n -o SOURCE -T "$candidate")"
  [[ "$dev" == /dev/* ]] || continue
  dev="$(readlink -f "$dev")"
  [[ -b "$dev" && "$dev" != "$system_source" ]] || continue
  physical_disk="$(lsblk -nrpo PKNAME "$dev" 2>/dev/null | head -n1 || true)"
  [[ -n "$physical_disk" ]] || physical_disk="$dev"
  [[ "$physical_disk" != "$system_disk" ]] || continue
  read -r freeblocks blocksize < <(stat -f -c '%a %S' "$candidate")
  (( freeblocks * blocksize >= 30 * 1024 * 1024 * 1024 )) || { echo "Skip $candidate (<30 GiB free)"; continue; }
  MOUNT="$candidate"
  break
done
if [[ -z "$MOUNT" ]]; then
  echo 'NO_EXTERNAL_BLOCK_DISK: training NOT started. Need separate ext4/xfs/btrfs volume, >=30 GiB free.' >&2
  findmnt -rn -o TARGET,SOURCE,FSTYPE | grep -E '/mnt/|/media/|VPS-Archief' || true
  exit 24
fi
ROOT="$MOUNT/fuckball-4v4-training"
me="$(id -un)"
group="$(id -gn)"
[[ ! -L "$ROOT" ]] || { echo 'Refuse symlink training root'; exit 25; }
sudo -n install -d -m 0750 -o "$me" -g "$group" "$ROOT"
for folder in releases state tmp cache home logs; do
  [[ ! -L "$ROOT/$folder" ]] || { echo "Refuse symlink $folder"; exit 26; }
  install -d -m 0750 "$ROOT/$folder"
done
export HOME="$ROOT/home" TMPDIR="$ROOT/tmp" TMP="$ROOT/tmp" TEMP="$ROOT/tmp"
export XDG_CACHE_HOME="$ROOT/cache" PIP_CACHE_DIR="$ROOT/cache/pip"
export PYTHONPYCACHEPREFIX="$ROOT/cache/pycache" NPM_CONFIG_CACHE="$ROOT/cache/npm"
[[ ! -e "$ROOT/state/STOP" ]] || { echo 'Operator STOP veto is present'; exit 27; }
if systemctl is-active --quiet fuckball-training.service; then
  echo 'Legacy trainer is already active; refuse overlapping process' >&2; exit 28
fi
if systemctl is-active --quiet "$UNIT"; then
  existing="$(systemctl show -p ExecStart --value "$UNIT")"
  if [[ "$existing" == *"$ROOT/releases/"*"/training/continuous.cjs"* ]]; then
    echo "Already running on the approved external mount: $ROOT"; exit 0
  fi
  echo 'Existing active service points to a different storage path' >&2; exit 29
fi
stage="$(mktemp -d -p "$ROOT/tmp" fuckball-bootstrap.XXXXXXXX)"
trap 'rm -rf -- "$stage"' EXIT
sha="$(git ls-remote "$REPO" refs/heads/main | awk '{print $1}')"
[[ "$sha" =~ ^[0-9a-f]{40}$ ]] || { echo 'Could not pin upstream main SHA'; exit 30; }
release="$ROOT/releases/$sha"
if [[ ! -d "$release/.git" ]]; then
  git clone --quiet --depth 1 --branch main "$REPO" "$stage/repo"
  [[ "$(git -C "$stage/repo" rev-parse HEAD)" == "$sha" ]] || { echo 'Source changed during clone'; exit 31; }
  node --check "$stage/repo/training/continuous.cjs"
  node --check "$stage/repo/training/haxlab_selfplay.cjs"
  node --check "$stage/repo/training/league.cjs"
  mv "$stage/repo" "$release"
fi
if [[ ! -x "$ROOT/venv/bin/python3" ]]; then python3 -m venv "$ROOT/venv"; fi
"$ROOT/venv/bin/python3" -m pip install --no-cache-dir --disable-pip-version-check 'numpy>=2.1,<3'
nodebin="$(command -v node)"
cat > "$stage/$UNIT" <<EOF
[Unit]
Description=Fuckball autonomous neural 4v4 research trainer - external disk
After=network-online.target
Wants=network-online.target
RequiresMountsFor=$MOUNT
ConditionPathIsMountPoint=$MOUNT
StartLimitIntervalSec=3600
StartLimitBurst=3

[Service]
Type=simple
User=$me
Group=$group
WorkingDirectory=$release
ExecStartPre=/usr/bin/mountpoint -q $MOUNT
ExecStart=$nodebin $release/training/continuous.cjs $ROOT/state forever
Environment=NODE_ENV=production
Environment=PYTHONUNBUFFERED=1
Environment=CODEX_PRIMARY_RUNTIME_PYTHON=$ROOT/venv/bin/python3
Environment=HOME=$ROOT/home
Environment=TMPDIR=$ROOT/tmp
Environment=TMP=$ROOT/tmp
Environment=TEMP=$ROOT/tmp
Environment=XDG_CACHE_HOME=$ROOT/cache
Environment=PYTHONPYCACHEPREFIX=$ROOT/cache/pycache
Environment=PIP_CACHE_DIR=$ROOT/cache/pip
Environment=NPM_CONFIG_CACHE=$ROOT/cache/npm
Environment=HAXLAB_EPISODES=48
Environment=HAXLAB_GENERATIONS=6
Environment=HAXLAB_LEAGUE_FRAMES=2400
Environment=HAXLAB_DISK_RESERVE_GIB=20
Environment=OPENBLAS_NUM_THREADS=2
Environment=OMP_NUM_THREADS=2
Restart=on-failure
RestartSec=90
CPUQuota=200%
MemoryMax=3G
TasksMax=80
Nice=12
IOSchedulingClass=best-effort
IOSchedulingPriority=7
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$ROOT
TemporaryFileSystem=/tmp:rw,nodev,nosuid,size=256M
TemporaryFileSystem=/var/tmp:rw,nodev,nosuid,size=128M
StandardOutput=append:$ROOT/logs/training.log
StandardError=append:$ROOT/logs/training.log
TimeoutStopSec=120

[Install]
WantedBy=multi-user.target
EOF
sudo -n install -o root -g root -m 0644 "$stage/$UNIT" "/etc/systemd/system/$UNIT"
sudo -n systemctl daemon-reload
sudo -n systemctl enable --now "$UNIT"
sleep 3
sudo -n systemctl is-active --quiet "$UNIT" || { echo 'Trainer did not start'; exit 32; }
[[ -s "$ROOT/state/status.json" ]] || { echo 'Trainer active but has no status yet'; exit 33; }
echo "TRAINING_STARTED host=$(hostname) repo_sha=$sha external_path=$ROOT"
sed -n '1,24p' "$ROOT/state/status.json"

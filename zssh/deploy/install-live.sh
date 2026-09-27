#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE_ROOT="${1:-}"
if [[ -z "$SOURCE_ROOT" ]]; then
  SOURCE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fi
SOURCE_ROOT="$(cd "$SOURCE_ROOT" && pwd)"
SOURCE="$SOURCE_ROOT/zssh"

if [[ ! -f "$SOURCE/server.mjs" || ! -f "$SOURCE/package.json" ]]; then
  echo "zSSH source tree is incomplete: $SOURCE" >&2
  exit 2
fi

if [[ "$(id -u)" -eq 0 ]]; then
  echo "Refusing to install zSSH as root; run as the dedicated target user." >&2
  exit 2
fi

NODE_BIN="$(command -v node || true)"
NPM_BIN="$(command -v npm || true)"
if [[ -z "$NODE_BIN" || -z "$NPM_BIN" ]]; then
  echo "Node.js and npm are required" >&2
  exit 2
fi
NODE_MAJOR="$("$NODE_BIN" -p 'Number(process.versions.node.split(".")[0])')"
if (( NODE_MAJOR < 20 )); then
  echo "Node.js >=20 required; found $("$NODE_BIN" --version)" >&2
  exit 2
fi

REPO_SHA="$(git -C "$SOURCE_ROOT" rev-parse HEAD 2>/dev/null || true)"
if [[ -z "$REPO_SHA" ]]; then
  echo "Source root must be a Git checkout" >&2
  exit 2
fi
if [[ -n "${ZSSH_EXPECTED_SHA:-}" && "$REPO_SHA" != "$ZSSH_EXPECTED_SHA" ]]; then
  echo "Refusing unexpected zCloud revision: expected $ZSSH_EXPECTED_SHA got $REPO_SHA" >&2
  exit 2
fi

BASE="$HOME/.local/share/zssh"
RELEASES="$BASE/releases"
CURRENT="$BASE/current"
STATE="$HOME/.local/state/zssh"
CONFIG="$HOME/.config/zssh"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT="$UNIT_DIR/zssh.service"
ENV_FILE="$CONFIG/gateway.env"
RELEASE="$RELEASES/$REPO_SHA"
STAGE="$RELEASES/.$REPO_SHA.stage.$$"

mkdir -p "$RELEASES" "$STATE" "$CONFIG" "$UNIT_DIR"
chmod 700 "$STATE" "$CONFIG"
trap 'rm -rf "$STAGE"' EXIT

if [[ ! -d "$RELEASE" ]]; then
  mkdir -p "$STAGE"
  cp -a "$SOURCE/." "$STAGE/"
  rm -rf "$STAGE/node_modules" "$STAGE/data"
  "$NPM_BIN" install --prefix "$STAGE" --omit=dev --ignore-scripts --no-audit --no-fund
  "$NPM_BIN" test --prefix "$STAGE"
  mv "$STAGE" "$RELEASE"
fi

if [[ ! -f "$ENV_FILE" ]]; then
  umask 077
  if command -v openssl >/dev/null 2>&1; then
    TOKEN="$(openssl rand -hex 32)"
  else
    TOKEN="$("$NODE_BIN" -e 'console.log(require("crypto").randomBytes(32).toString("hex"))')"
  fi
  cat > "$ENV_FILE" <<EOF
NODE_ENV=production
PORT=8788
ZSSH_DEV_BEARER_TOKEN=$TOKEN
ZSSH_ALLOWED_ROOTS=/home/ubuntu/zennay-cloud
ZSSH_EXEC_MODE=disabled
ZSSH_SAFE_PROGRAMS=uptime,whoami,id,uname,pwd,df,free
ZSSH_COMMAND_TIMEOUT_SECONDS=30
ZSSH_MAX_OUTPUT_BYTES=131072
ZSSH_MAX_FILE_BYTES=131072
ZSSH_AUDIT_LOG=/home/ubuntu/.local/state/zssh/audit.jsonl
EOF
fi
chmod 600 "$ENV_FILE"

sed "s|@NODE_BIN@|$NODE_BIN|g" "$SOURCE/deploy/zssh.service.in" > "$UNIT"
chmod 600 "$UNIT"

PREVIOUS=""
if [[ -L "$CURRENT" ]]; then
  PREVIOUS="$(readlink -f "$CURRENT" || true)"
fi
TMP_LINK="$BASE/.current.$$"
ln -s "$RELEASE" "$TMP_LINK"
mv -Tf "$TMP_LINK" "$CURRENT"

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$XDG_RUNTIME_DIR/bus}"

rollback_release() {
  echo "zSSH live validation failed; restoring previous release" >&2
  if [[ -n "$PREVIOUS" && -d "$PREVIOUS" ]]; then
    local rollback_link="$BASE/.rollback.$$"
    ln -s "$PREVIOUS" "$rollback_link"
    mv -Tf "$rollback_link" "$CURRENT"
    systemctl --user daemon-reload || true
    systemctl --user restart zssh.service || true
  else
    systemctl --user disable --now zssh.service || true
    rm -f "$CURRENT"
  fi
}

systemctl --user daemon-reload
systemctl --user enable zssh.service >/dev/null
if ! systemctl --user restart zssh.service; then
  rollback_release
  exit 2
fi

HEALTH_OK=0
for _ in $(seq 1 20); do
  if curl --fail --silent --show-error "http://127.0.0.1:8788/health" >/dev/null; then
    HEALTH_OK=1
    break
  fi
  sleep 1
done
if [[ "$HEALTH_OK" != "1" ]]; then
  systemctl --user status zssh.service --no-pager || true
  rollback_release
  exit 2
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a
if ! "$NODE_BIN" "$RELEASE/live-canary.mjs"; then
  rollback_release
  exit 2
fi

systemctl --user is-active --quiet zssh.service
printf 'ZSSH_INSTALL_GREEN sha=%s release=%s\n' "$REPO_SHA" "$RELEASE"

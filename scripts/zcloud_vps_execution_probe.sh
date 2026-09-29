#!/usr/bin/env bash
set -euo pipefail

# Non-destructive capability probe for the shared zCloud VPS execution lane.
# This is intentionally a probe, not an arbitrary remote-command endpoint.

host="$(hostname -s 2>/dev/null || hostname)"
user="$(id -un)"
runner_pid="$$"

has_cmd() {
  command -v "$1" >/dev/null 2>&1
}

optional_version() {
  if has_cmd "$1"; then
    "$@" 2>&1 | head -n 1 | tr '\n' ' ' || true
  else
    printf 'missing'
  fi
}

printf 'ZCLOUD_VPS_EXECUTION_PROBE host=%s user=%s pid=%s\n' "$host" "$user" "$runner_pid"
printf 'ZCLOUD_VPS_CAPABILITY ssh=%s\n' "$(has_cmd ssh && echo yes || echo no)"
printf 'ZCLOUD_VPS_CAPABILITY git=%s\n' "$(has_cmd git && echo yes || echo no)"
printf 'ZCLOUD_VPS_CAPABILITY python3=%s\n' "$(has_cmd python3 && echo yes || echo no)"
printf 'ZCLOUD_VPS_CAPABILITY systemctl=%s\n' "$(has_cmd systemctl && echo yes || echo no)"
printf 'ZCLOUD_VPS_CAPABILITY docker=%s\n' "$(has_cmd docker && echo yes || echo no)"
printf 'ZCLOUD_VPS_CAPABILITY sudo_noninteractive=%s\n' "$(sudo -n true >/dev/null 2>&1 && echo yes || echo no)"
printf 'ZCLOUD_VPS_VERSION ssh=%s\n' "$(optional_version ssh -V)"
printf 'ZCLOUD_VPS_VERSION git=%s\n' "$(optional_version git --version)"
printf 'ZCLOUD_VPS_VERSION python3=%s\n' "$(optional_version python3 --version)"
printf 'ZCLOUD_VPS_PROBE=GREEN\n'

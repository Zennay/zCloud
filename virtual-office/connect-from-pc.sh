#!/usr/bin/env bash
# Run on your OWN Mac/Linux computer, not on the VPS.
# The Office access code travels inside SSH, never over the public HTTP port.
set -euo pipefail
target="${1:-${OFFICE_SSH_TARGET:-}}"
if [[ -z "$target" || "$target" == -* ]]; then
  echo "Gebruik: bash connect-from-pc.sh <ssh-gebruiker>@198.244.191.182" >&2
  echo "Of gebruik je bestaande SSH-alias: bash connect-from-pc.sh <alias>" >&2
  exit 2
fi
if ! command -v ssh >/dev/null 2>&1; then
  echo "OpenSSH is niet beschikbaar op deze computer." >&2
  exit 2
fi
echo "Office via eigen pc: http://127.0.0.1:3422/"
echo "Tampermonkey in een normale ChatGPT-browser-tab: http://127.0.0.1:3422"
echo "De tunnel blijft actief zolang dit venster open is. Stop met Ctrl+C."
exec ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=25 -o ServerAliveCountMax=3 \
  -L 127.0.0.1:3422:127.0.0.1:3422 -- "$target"

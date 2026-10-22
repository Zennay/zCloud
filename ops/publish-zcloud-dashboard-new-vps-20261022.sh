#!/usr/bin/env bash
# New VPS: publish an ALREADY-HEALTHY zCloud API under authenticated HTTPS.
# Read-only by default; intentionally does NOT start SQLite/scheduler/workers.
# Usage: bash ops/publish-zcloud-dashboard-new-vps-20261022.sh --status
#        ZCLOUD_SQLITE_CUTOVER_CONFIRMED=yes sudo -E bash ops/publish-zcloud-dashboard-new-vps-20261022.sh --apply
set -Eeuo pipefail

MODE="$1"
HOST='VM-7eb5ee16-2c71-440d-b7fd-8095d204e930'
IP='95.211.40.88'
DOMAIN='zcloud.zennay.nl'
CADDYFILE='/etc/caddy/Caddyfile'
SITES='/etc/caddy/sites-enabled'
SITE='/etc/caddy/sites-enabled/zcloud-dashboard.caddy'
AUTH='/etc/caddy/zcloud-dashboard-auth.caddy'
IMPORT='import /etc/caddy/sites-enabled/*'

die() { printf 'REFUSED: %s\n' "$*" >&2; exit 2; }
say() { printf '%s\n' "$*"; }
[[ "$MODE" = --status || "$MODE" = --apply ]] || die 'usage: --status | --apply'
[[ "$(hostname)" = "$HOST" ]] || die 'wrong host'
for cmd in curl python3 ss getent caddy systemctl stat flock; do
  command -v "$cmd" >/dev/null || die "missing command: $cmd"
done

backend_ready() {
  systemctl is-active --quiet zennay-cloud.service || return 1
  local sockets
  sockets="$(ss -H -ltn '( sport = :8765 )' | awk '{print $4}' | sort -u)"
  [[ "$sockets" = '127.0.0.1:8765' ]] || return 1
  curl --noproxy '*' --fail --silent --max-time 5 \
    http://127.0.0.1:8765/api/status |
    python3 -c 'import json,sys; x=json.load(sys.stdin); assert isinstance(x,dict) and x.get("time") and isinstance(x.get("projects"),list)'
}

dns_ready() {
  [[ "$(getent ahostsv4 "$DOMAIN" | awk '{print $1}' | sort -u)" = "$IP" ]]
}

import_ready() {
  [[ -f "$CADDYFILE" && ! -L "$CADDYFILE" && -d "$SITES" && ! -L "$SITES" ]] &&
    grep -Fxq "$IMPORT" "$CADDYFILE"
}

auth_ready() {
  [[ -f "$AUTH" && ! -L "$AUTH" ]] || return 1
  [[ "$(stat -c '%U' "$AUTH")" = root ]] || return 1
  [[ "$(stat -c '%G:%a' "$AUTH")" = caddy:640 ]] || return 1
  # Single bcrypt basic_auth block only; other Caddy directives are rejected.
  python3 - "$AUTH" <<'PY'
import pathlib, re, sys
lines = [s.strip() for s in pathlib.Path(sys.argv[1]).read_text().splitlines()
         if s.strip() and not s.lstrip().startswith('#')]
if len(lines) != 3 or lines[0] != 'basic_auth {' or lines[2] != '}':
    raise SystemExit(1)
if not re.fullmatch(r'[A-Za-z0-9_.@-]{1,64}\s+\$2[aby]\$\d{2}\$[./A-Za-z0-9]{53}', lines[1]):
    raise SystemExit(1)
PY
}

public_requires_login() {
  local code
  code="$(curl --noproxy '*' --silent --output /dev/null --max-time 12 \
    --write-out '%{http_code}' "https://$DOMAIN/" || true)"
  [[ "$code" = 401 ]]
}

say "target=$HOST ipv4=$IP domain=$DOMAIN"
if backend_ready; then say 'backend=healthy_loopback'; else say 'backend=not_ready'; fi
if dns_ready; then say 'dns=target_ipv4'; else say 'dns=not_target'; fi
if import_ready; then say 'caddy_import=ready'; else say 'caddy_import=missing'; fi
if auth_ready; then say 'auth=valid'; else say 'auth=missing_or_invalid'; fi
if systemctl is-active --quiet caddy; then say 'caddy=active'; else say 'caddy=inactive'; fi
if public_requires_login; then say 'external_https=401_auth_required'; else say 'external_https=not_proven'; fi

[[ "$MODE" = --apply ]] || exit 0
[[ "$(printenv ZCLOUD_SQLITE_CUTOVER_CONFIRMED || true)" = yes ]] ||
  die 'old-VPS writer fence and final SQLite handoff must be independently confirmed'
[[ "$EUID" -eq 0 ]] || die 'root required'
exec 9>/run/lock/zcloud-dashboard-publication.lock
flock -n 9 || die 'another dashboard publisher is active'
backend_ready || die 'API not active, healthy and loopback-only'
systemctl is-active --quiet caddy || die 'Caddy inactive'
dns_ready || die 'DNS not cut over to new VPS'
import_ready || die 'Caddy owner must review and enable sites-enabled import first'
auth_ready || die 'root-owned bcrypt auth snippet missing or invalid; never publish credentials'
if [[ -e "$SITE" || -L "$SITE" ]]; then
  public_requires_login && { say 'ALREADY_LIVE_AUTH_REQUIRED'; exit 0; }
  die 'existing Caddy site is not verified'
fi

# Stage outside the imported directory to avoid half-written public config.
STAGED="$(mktemp /etc/caddy/.zcloud-dashboard-staged.XXXXXXXX)"
cat >"$STAGED" <<'CADDY'
zcloud.zennay.nl {
    import /etc/caddy/zcloud-dashboard-auth.caddy
    encode zstd gzip
    header {
        X-Content-Type-Options "nosniff"
        Referrer-Policy "strict-origin-when-cross-origin"
        X-Frame-Options "DENY"
    }
    reverse_proxy 127.0.0.1:8765
}
CADDY
chmod 0644 "$STAGED"
ln "$STAGED" "$SITE" || die 'site already occupied (no overwrite)'
unlink "$STAGED"
if ! caddy validate --config "$CADDYFILE" --adapter caddyfile >/dev/null 2>&1; then
  mv "$SITE" "/etc/caddy/zcloud-dashboard-validation-failed-$(date +%s)-$.caddy"
  die 'Caddy validation failed; active import removed'
fi
if ! systemctl reload caddy; then
  mv "$SITE" "/etc/caddy/zcloud-dashboard-reload-failed-$(date +%s)-$.caddy"
  systemctl reload caddy || true
  die 'Caddy reload failed; new site removed from import'
fi
if ! public_requires_login; then
  mv "$SITE" "/etc/caddy/zcloud-dashboard-auth-failed-$(date +%s)-$.caddy"
  systemctl reload caddy || true
  die 'public HTTPS did not require authentication; site rolled back'
fi
say 'ZCLOUD_DASHBOARD_PUBLIC_HTTPS_AUTH_GREEN'
say 'Confirm authenticated HTML and /api/status independently from outside the VPS.'

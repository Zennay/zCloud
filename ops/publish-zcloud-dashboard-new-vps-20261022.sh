#!/usr/bin/env bash
# New VPS: publish an ALREADY-HEALTHY zCloud API under authenticated HTTPS.
# Read-only by default; intentionally does NOT start SQLite/scheduler/workers.
# Usage: bash ops/publish-zcloud-dashboard-new-vps-20261022.sh --status
#        ZCLOUD_DNS_CUTOVER_CONFIRMED=yes ZCLOUD_SQLITE_CUTOVER_CONFIRMED=yes sudo -E bash ops/publish-zcloud-dashboard-new-vps-20261022.sh --apply
set -Eeuo pipefail

MODE="${1:---status}"
HOST='VM-7eb5ee16-2c71-440d-b7fd-8095d204e930'
IP='95.211.40.88'
DOMAIN='zcloud.zennay.nl'
CADDYFILE='/etc/caddy/Caddyfile'
SITES='/etc/caddy/sites-enabled'
SITE='/etc/caddy/sites-enabled/zcloud-dashboard.caddy'
QUARANTINE='/var/lib/zcloud-dashboard-quarantine'
AUTH='/etc/caddy/zcloud-dashboard-auth.caddy'
IMPORT='import /etc/caddy/sites-enabled/*'

die() { printf 'REFUSED: %s\n' "$*" >&2; exit 2; }
[[ $# -le 1 ]] || die 'usage: --status | --apply'
say() { printf '%s\n' "$*"; }
[[ "$MODE" = --status || "$MODE" = --verify || "$MODE" = --apply ]] || die 'usage: --status | --verify | --apply'
[[ "$(hostname)" = "$HOST" ]] || die 'wrong host'
for cmd in curl python3 ss getent caddy systemctl stat flock awk sort grep mktemp chmod ln unlink date mv mkdir rm cat; do
  command -v "$cmd" >/dev/null || die "missing command: $cmd"
done

backend_ready() {
  systemctl is-active --quiet zennay-cloud.service || return 1
  local sockets
  sockets="$(ss -H -ltn '( sport = :8765 )' | awk '{print $4}' | sort -u)"
  [[ "$sockets" = '127.0.0.1:8765' ]] || return 1
  curl --noproxy '*' --fail --silent --max-time 5 \
    http://127.0.0.1:8765/api/status |
    python3 -c 'import datetime,json,sys; x=json.load(sys.stdin); assert isinstance(x,dict) and isinstance(x.get("projects"),list); raw=x.get("time"); assert isinstance(raw,str) and raw; t=datetime.datetime.fromisoformat(raw.replace("Z","+00:00")); assert t.tzinfo is not None and t.utcoffset() is not None; age=(datetime.datetime.now(datetime.timezone.utc)-t.astimezone(datetime.timezone.utc)).total_seconds(); assert -120 <= age <= 300'
}

dns_ready() {
  # Require the complete address set to be exactly the approved IPv4 target.
  # Any AAAA/IPv6 result fails closed until a separately approved IPv6 route exists.
  local addresses
  addresses="$(getent ahosts "$DOMAIN" | awk '{print $1}' | sort -u)"
  [[ "$addresses" = "$IP" ]]
}

import_ready() {
  [[ -f "$CADDYFILE" && ! -L "$CADDYFILE" && -d "$SITES" && ! -L "$SITES" ]] || return 1
  [[ "$(stat -c '%U' "$CADDYFILE")" = root && "$(stat -c '%U' "$SITES")" = root ]] || return 1
  local caddy_mode sites_mode
  caddy_mode="$(stat -c '%a' "$CADDYFILE")" || return 1
  sites_mode="$(stat -c '%a' "$SITES")" || return 1
  (( (8#$caddy_mode & 0022) == 0 )) || return 1
  (( (8#$sites_mode & 0022) == 0 )) || return 1
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
  # A protected homepage is not enough: privileged JSON API routes must also
  # be denied to unauthenticated callers. Do not follow redirects or supply
  # credentials; any 200/3xx/403/5xx is not proof of the intended auth gate.
  local path code
  for path in / /api/status /api/portfolio-queue /api/runner-targets; do
    code="$(curl --proto '=https' --noproxy '*' --silent --output /dev/null --max-time 12 \
      --write-out '%{http_code}' "https://$DOMAIN$path" || true)"
    [[ "$code" = 401 ]] || return 1
  done
}

# Optional read-only authenticated end-to-end check. An operator may provide
# a root-owned 0600 netrc file; never place secrets in arguments, logs, PRs or
# service environment. This is a verification option, not a deploy grant.
authenticated_dashboard_ready() {
  local netrc="${ZCLOUD_DASHBOARD_NETRC:-}" metadata
  [[ "$EUID" -eq 0 && "$netrc" == /* && -f "$netrc" && ! -L "$netrc" ]] || return 1
  metadata="$(stat -c '%U:%G:%a:%s' -- "$netrc")" || return 1
  case "$metadata" in root:root:600:*) ;; *) return 1 ;; esac
  [[ "${metadata##*:}" =~ ^[0-9]+$ ]] || return 1
  (( ${metadata##*:} > 0 && ${metadata##*:} <= 4096 )) || return 1

  curl --proto '=https' --noproxy '*' --fail --silent --max-time 12 \
    --max-filesize 1048576 --netrc-file "$netrc" "https://$DOMAIN/" |
    python3 -c 'import sys; b=sys.stdin.buffer.read(1048577); t=b.decode("utf-8","replace").lower(); assert len(b)<=1048576 and "<html" in t and "</html>" in t and "<title>zcloud" in t' || return 1
  curl --proto '=https' --noproxy '*' --fail --silent --max-time 12 \
    --max-filesize 2097152 --netrc-file "$netrc" "https://$DOMAIN/api/status" |
    python3 -c 'import datetime,json,sys; b=sys.stdin.buffer.read(2097153); assert len(b)<=2097152; x=json.loads(b); assert isinstance(x,dict) and isinstance(x.get("projects"),list); t=datetime.datetime.fromisoformat(x["time"].replace("Z","+00:00")); assert t.tzinfo and t.utcoffset() is not None; age=(datetime.datetime.now(datetime.timezone.utc)-t.astimezone(datetime.timezone.utc)).total_seconds(); assert -120<=age<=300' || return 1
}

SITE_ID=''
quarantine_dir_ready() {
  [[ ! -L "$QUARANTINE" ]] || return 1
  if [[ ! -e "$QUARANTINE" ]]; then
    mkdir -m 0700 -- "$QUARANTINE" || return 1
  fi
  [[ -d "$QUARANTINE" && ! -L "$QUARANTINE" ]] || return 1
  [[ "$(stat -c '%U:%G:%a' "$QUARANTINE")" = 'root:root:700' ]] || return 1
  # A same-filesystem move is atomic; fail closed if /var/lib is a separate mount.
  [[ "$(stat -c '%d' "$QUARANTINE")" = "$(stat -c '%d' "$SITES")" ]] || return 1
}
quarantine_own_site() {
  local reason="$1" current_id destination
  [[ -n "$SITE_ID" && -f "$SITE" && ! -L "$SITE" ]] || return 1
  current_id="$(stat -c '%d:%i' "$SITE")" || return 1
  [[ "$current_id" = "$SITE_ID" ]] || return 1
  destination="$QUARANTINE/zcloud-dashboard-${reason}-$(date +%s)-$BASHPID.caddy"
  [[ ! -e "$destination" && ! -L "$destination" ]] || return 1
  mv -n -- "$SITE" "$destination" || return 1
  [[ ! -e "$SITE" && ! -L "$SITE" && -f "$destination" ]] || return 1
  say "rollback_quarantine=$destination"
}

say "target=$HOST ipv4=$IP domain=$DOMAIN"
if backend_ready; then say 'backend=healthy_loopback'; else say 'backend=not_ready'; fi
if dns_ready; then say 'dns=resolver_addresses_target_ipv4_no_ipv6'; else say 'dns=resolver_addresses_mismatch_or_ipv6'; fi
if import_ready; then say 'caddy_import=ready'; else say 'caddy_import=missing'; fi
if auth_ready; then say 'auth=valid'; else say 'auth=missing_or_invalid'; fi
if systemctl is-active --quiet caddy; then say 'caddy=active'; else say 'caddy=inactive'; fi
if public_requires_login; then say 'external_https=homepage_and_critical_api_auth_required'; else say 'external_https=auth_not_proven_for_all_paths'; fi

if [[ "$MODE" = --verify ]]; then
  backend_ready || die 'backend cannot be verified as fresh and loopback-only'
  dns_ready || die 'resolver DNS target does not match the approved host'
  public_requires_login || die 'anonymous dashboard or critical API endpoint is not protected'
  authenticated_dashboard_ready || die 'authenticated HTML/API response not verified; check private root-owned netrc file'
  say 'authenticated_dashboard=HTML_AND_FRESH_API_VERIFIED'
  exit 0
fi

[[ "$MODE" = --apply ]] || exit 0
[[ "$(printenv ZCLOUD_DNS_CUTOVER_CONFIRMED || true)" = yes ]] ||
  die 'DNS owner must independently confirm current delegation and intended A/AAAA records'
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
  if public_requires_login; then
    die 'existing site returns HTTP 401, but authenticated dashboard behavior is not verified; no changes made'
  fi
  die 'existing Caddy site is not verified; no changes made'
fi
quarantine_dir_ready || die 'cannot prepare a root-owned, same-filesystem quarantine outside the Caddy config tree'

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
STAGED_ID="$(stat -c '%d:%i' "$STAGED")" || die 'cannot identify staged site file'
ln "$STAGED" "$SITE" || die 'site already occupied (no overwrite)'
SITE_ID="$STAGED_ID"
if ! unlink "$STAGED"; then
  quarantine_own_site staging-cleanup-failed || die 'staging cleanup failed and the new site could not be safely removed; live state is unverified'
  die 'staging cleanup failed; the new site was removed from active imports'
fi
if ! caddy validate --config "$CADDYFILE" --adapter caddyfile >/dev/null 2>&1; then
  quarantine_own_site validation-failed || die 'Caddy validation failed and the new site could not be safely removed; manual recovery required'
  die 'Caddy validation failed; this run site was removed from the imported directory'
fi
if ! systemctl reload caddy; then
  quarantine_own_site reload-failed || die 'Caddy reload failed and the new site could not be safely removed; live state is unverified'
  if ! systemctl reload caddy; then
    die 'Caddy reload failed; site removed from imports but rollback reload is not verified'
  fi
  die 'Caddy reload failed; new site removed and previous config reload succeeded'
fi
if ! public_requires_login; then
  quarantine_own_site auth-failed || die 'public HTTPS auth check failed and the new site could not be safely removed; live state is unverified'
  if ! systemctl reload caddy; then
    die 'public HTTPS auth check failed; site removed from imports but rollback reload failed; live state is unverified'
  fi
  die 'public HTTPS did not require authentication; site removed and previous config reloaded'
fi
say 'ZCLOUD_DASHBOARD_PUBLIC_HTTPS_AUTH_GREEN'
say 'Confirm authenticated HTML and /api/status independently from outside the VPS.'

#!/usr/bin/env bash
# Promote only a merged and CI-approved native Fuckball release.
# The protected Work-session checkout /opt/fuckball and game data are never modified.
set -Eeuo pipefail

die() { echo "::error::$*" >&2; exit 1; }
: "${TARGET_SHA:?Required immutable target SHA missing}"
: "${RELEASE_RUN:?GitHub run ID missing}"
: "${RELEASE_ATTEMPT:?Run attempt missing}"
[[ "$TARGET_SHA" =~ ^[a-f0-9]{40}$ ]] || die "Invalid commit SHA"
[[ "$RELEASE_RUN" =~ ^[0-9]+$ && "$RELEASE_ATTEMPT" =~ ^[0-9]+$ ]] || die "Invalid run identity"

root=/opt/fuckball-releases
unit=/etc/systemd/system/fuckball.service
port=3417
node_binary="$(command -v node)"
[[ "$(node --version)" =~ ^v22\. ]] || die "Native release requires Node 22"
[[ -f "$unit" && -d "$root" ]] || die "Native immutable release directories not present"
sudo systemctl is-active --quiet fuckball.service || die "Existing service not healthy; requires separate incident response"

existing_dir="$(systemctl show fuckball.service --value -p WorkingDirectory)"
[[ "$existing_dir" == "$root/"* && -f "$existing_dir/server.js" ]] ||
  die "Live service does not point to an existing immutable native release"
grep -Fxq 'StateDirectory=fuckball' "$unit" || die "Refusing to replace a unit that does not preserve persistent account/game state"
grep -Fxq 'NoNewPrivileges=true' "$unit" || die "Refusing to weaken service protection"
grep -Fxq 'ProtectSystem=strict' "$unit" || die "Refusing to weaken filesystem sandbox"

get_health() {
  curl --fail --silent --show-error --max-time 4 "http://127.0.0.1:$port/health"
}
live_health="$(get_health)" || die "Current native service health endpoint unavailable"
read_rooms() {
  printf '%s' "$1" | node -e "let s='';process.stdin.on('data',d=>s+=d);process.stdin.on('end',()=>{const h=JSON.parse(s);if(h.ok!==true||h.engine!=='node-haxball-2.3.1'||!Number.isSafeInteger(h.rooms)||h.rooms<0)process.exit(2);process.stdout.write(String(h.rooms))})"
}
read_release() {
  printf '%s' "$1" | node -e "let s='';process.stdin.on('data',d=>s+=d);process.stdin.on('end',()=>{const h=JSON.parse(s);if(!/^[0-9a-f]{40}$/.test(h.release))process.exit(2);process.stdout.write(h.release)})"
}
live_rooms="$(read_rooms "$live_health")" || die "Live room count could not be authenticated"
live_sha="$(read_release "$live_health")" || die "Live release identity missing"
if [[ "$live_sha" == "$TARGET_SHA" ]]; then
  echo "Native release $TARGET_SHA is already serving traffic; nothing to restart."
  exit 0
fi
[[ "$live_rooms" == 0 ]] || die "$live_rooms live rooms active; refusing to interrupt players"

remote_sha="$(git ls-remote https://github.com/Zennay/fuckball.git refs/heads/main | awk '{print $1}')"
[[ "$remote_sha" == "$TARGET_SHA" ]] || die "Main advanced after CI authorisation; re-dispatch with new exact SHA"
stage="$root/$TARGET_SHA-$RELEASE_RUN-$RELEASE_ATTEMPT"
[[ ! -e "$stage" ]] || die "Immutable release directory already exists, do not overwrite"
sudo install -d -o "$(id -un)" -g "$(id -gn)" -m 0755 "$stage"
git clone --quiet --depth 1 --branch main https://github.com/Zennay/fuckball.git "$stage"
[[ "$(git -C "$stage" rev-parse HEAD)" == "$TARGET_SHA" ]] ||
  die "Cloned source does not match authorised commit"
[[ -f "$stage/headless/server.cjs" && -s "$stage/dist/vendor/haxball.js" &&
   -s "$stage/dist/stadiums/bff-big-v4.hbs" ]] ||
  die "Native engine or approved BFF stadium missing from staged release"
node --check "$stage/server.js"
node --check "$stage/headless/server.cjs"

work="${RUNNER_TEMP:-/tmp}/fuckball-release-$RELEASE_RUN-$RELEASE_ATTEMPT"
mkdir -p "$work"
cp "$unit" "$work/previous.service"
sed -e "s|^WorkingDirectory=.*|WorkingDirectory=$stage|" \
    -e "s|^ExecStart=.*|ExecStart=$node_binary $stage/server.js|" \
    -e "s|^Environment=FUCKBALL_RELEASE_SHA=.*|Environment=FUCKBALL_RELEASE_SHA=$TARGET_SHA|" \
    "$unit" > "$work/fuckball.service"
grep -Fxq "WorkingDirectory=$stage" "$work/fuckball.service" || die "Unit workdir replacement failed"
grep -Fxq "ExecStart=$node_binary $stage/server.js" "$work/fuckball.service" || die "Native unit ExecStart replacement failed"
grep -Fxq "Environment=FUCKBALL_RELEASE_SHA=$TARGET_SHA" "$work/fuckball.service" || die "Release provenance missing from unit"
grep -Fxq 'StateDirectory=fuckball' "$work/fuckball.service" || die "Persistent state lost in new unit"
systemd-analyze verify "$work/fuckball.service" || die "Native service unit invalid"

# Revalidate live admission immediately before a controlled service restart.
before="$(get_health)" || die "Service health changed during preparation"
[[ "$(read_rooms "$before")" == 0 ]] || die "A live room started while staging; refusing to interrupt"
[[ "$(read_release "$before")" == "$live_sha" ]] || die "Service release changed during staging; refusing concurrent deploy"

switched=0
rollback() {
  local result=$?
  trap - EXIT
  if [[ "$switched" == 1 ]]; then
    echo "::warning::Promoted release failed. Restoring previous systemd unit and immutable source."
    sudo install -m 0644 "$work/previous.service" "$unit" || true
    sudo systemctl daemon-reload || true
    sudo systemctl restart fuckball.service || true
    get_health || true
  fi
  exit "$result"
}
trap rollback EXIT

switched=1
sudo install -m 0644 "$work/fuckball.service" "$unit"
sudo systemctl daemon-reload
sudo systemctl restart fuckball.service
ready=0
for attempt in {1..20}; do
  if health="$(get_health 2>/dev/null)"; then
    release="$(read_release "$health" 2>/dev/null || true)"
    if [[ "$release" == "$TARGET_SHA" ]]; then ready=1;break;fi
  fi
  sleep 1
done
[[ "$ready" == 1 ]] || die "New release never reported the exact deployed commit"
sudo systemctl is-active --quiet fuckball.service || die "New service not active"
curl --fail --silent --show-error --max-time 5 "http://127.0.0.1:$port/" -o "$work/deployed.html" ||
  die "Native home page not served"
grep -Fq 'career-profile.js' "$work/deployed.html" ||
  die "New native client assets not being served"
curl --fail --silent --show-error --max-time 5 "http://127.0.0.1:$port/dist/room-browser.js" -o /dev/null ||
  curl --fail --silent --show-error --max-time 5 "http://127.0.0.1:$port/room-browser.js" -o /dev/null ||
  die "Room browser asset not served"

# The release directory is immutable evidence; the previous release and
# persistent state are preserved so a manual rollback remains possible.
switched=0
trap - EXIT
echo "Deployed Fuckball native main $TARGET_SHA (from $live_sha)."
echo "Persistent accounts/club/match state preserved in systemd StateDirectory=fuckball."
{
  echo "## Fuckball native VPS release"
  echo "- Source: [`$TARGET_SHA`](https://github.com/Zennay/fuckball/commit/$TARGET_SHA)"
  echo "- Previous live SHA: `$live_sha`"
  echo "- Immutable release directory: `$stage`"
  echo "- Native health exact SHA confirmed; approved physics and data untouched."
} >> "${GITHUB_STEP_SUMMARY:-/dev/null}"

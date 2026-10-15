#!/usr/bin/env bash
# Persistent, independent ChatGPT relay browser on the Ubuntu VPS desktop.
# Never reuse the zCloud worker profile: workers own their assigned tabs.
set -euo pipefail

if [[ "$(id -un)" != "ubuntu" ]]; then
  echo "Start Office Relay onder de normale VPS-desktopgebruiker ubuntu." >&2
  exit 2
fi
export DISPLAY="${OFFICE_RELAY_DISPLAY:-:10}"
export XAUTHORITY="${XAUTHORITY:-/home/ubuntu/.Xauthority}"
export MOZ_ENABLE_WAYLAND=0

# The existing xrdp-visible desktop is Xorg :10. Do not quietly fall back to
# Xvfb :99; the owner must be able to log in and manage the ChatGPT session.
if [[ "$DISPLAY" != ":10" ]]; then
  echo "Office Relay vereist het bestaande zichtbare VPS-desktopdisplay :10." >&2
  exit 2
fi
if [[ ! -S /tmp/.X11-unix/X10 ]]; then
  echo "De VPS RDP-desktop (:10) draait niet. Log eerst via RDP in." >&2
  exit 1
fi

browser="${OFFICE_RELAY_FIREFOX:-/usr/bin/firefox}"
if [[ ! -x "$browser" ]]; then
  echo "Firefox niet gevonden: $browser" >&2
  exit 1
fi

# Snap Firefox accesses this profile under ~/snap/firefox/common. Avoid the
# zCloud automation/web-ext Firefox profile, even if another browser is open.
profile="${HOME}/snap/firefox/common/office-relay-vps-profile"
mkdir -p "$profile"
chmod 0700 "$profile"
if [[ ! -f "$profile/user.js" ]]; then
  cat > "$profile/user.js" <<'PREFS'
user_pref("browser.startup.homepage", "https://chatgpt.com/");
user_pref("browser.startup.page", 3);
user_pref("browser.sessionstore.resume_from_crash", true);
PREFS
  chmod 0600 "$profile/user.js"
fi

echo "Office Relay Firefox op VPS display $DISPLAY; apart profiel: $profile" >&2
echo "Eerste keer: installeer Tampermonkey, meld je aan op ChatGPT en verbind" >&2
echo "iedere medewerker met http://127.0.0.1:3422 (in DE VPS-browser)." >&2
# Optional: open each employee's saved ChatGPT conversation after a restart, so the
# Tampermonkey relay in that tab can reconnect by itself. One https://chatgpt.com/c/<id>
# URL per line (maya, noah, lina). Nothing else is ever opened from this file.
tabs_file="${OFFICE_RELAY_TABS_FILE:-${HOME}/.config/virtual-office/relay-tabs.txt}"
urls=()
if [[ -f "$tabs_file" ]]; then
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line//$'\r'/}"
    if [[ "$line" =~ ^https://chatgpt\.com/c/[A-Za-z0-9-]{8,64}$ ]] && (( ${#urls[@]} < 3 )); then
      urls+=("$line")
    fi
  done < "$tabs_file"
fi
echo "Chat-tabs uit $tabs_file: ${#urls[@]}" >&2
# Separate Firefox process; do not attach to/reconfigure the active worker.
exec "$browser" --no-remote --profile "$profile" "${urls[@]}"

#!/usr/bin/env bash
# Install a macOS-style KDE desktop for the 'desktop' XRDP user on the NEW VPS.
# Run as root. Does not change the display manager, XRDP or .xsession.
set -Eeuo pipefail

EXPECTED_HOST="VM-7eb5ee16-2c71-440d-b7fd-8095d204e930"
TARGET_USER="desktop"
TARGET_HOME="/home/$TARGET_USER"
if [[ "$(hostname)" != "$EXPECTED_HOST" ]]; then
  echo "REFUSED: this installer is for $EXPECTED_HOST, not $(hostname)." >&2
  exit 1
fi
if (( EUID != 0 )); then
  echo "Run with sudo or as root." >&2
  exit 1
fi
id "$TARGET_USER" >/dev/null 2>&1 || { echo "Missing user: $TARGET_USER" >&2; exit 1; }

as_desktop() { runuser -u "$TARGET_USER" -- env HOME="$TARGET_HOME" "$@"; }
echo "[1/6] Dependencies and fonts"
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y git curl ca-certificates jq tar fontconfig
for pkg in fonts-inter qt6-style-kvantum qt5-style-kvantum qdbus-qt6; do
  if apt-cache show "$pkg" >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y "$pkg" || echo "Optional package skipped: $pkg"
  fi
done
fc-cache -f >/dev/null 2>&1 || true

echo "[2/6] Back up desktop appearance configs"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="$TARGET_HOME/.local/share/macos-vps-backups/$STAMP"
as_desktop mkdir -p "$BACKUP"
for cfg in kdeglobals kcminputrc plasmarc kwinrc plasma-org.kde.plasma.desktop-appletsrc; do
  if [[ -f "$TARGET_HOME/.config/$cfg" ]]; then
    as_desktop cp -a "$TARGET_HOME/.config/$cfg" "$BACKUP/$cfg"
  fi
done
echo "Backup: $BACKUP"

echo "[3/6] WhiteSur KDE, icons and cursors"
SOURCE_DIR="$(as_desktop mktemp -d)"
trap 'rm -rf -- "$SOURCE_DIR"' EXIT
as_desktop git clone --depth=1 --quiet https://github.com/vinceliuice/WhiteSur-kde.git "$SOURCE_DIR/kde"
as_desktop git clone --depth=1 --quiet https://github.com/vinceliuice/WhiteSur-icon-theme.git "$SOURCE_DIR/icons"
as_desktop git clone --depth=1 --quiet https://github.com/vinceliuice/WhiteSur-cursors.git "$SOURCE_DIR/cursors"
as_desktop bash -c 'cd "$1" && ./install.sh --color dark --window opaque' _ "$SOURCE_DIR/kde"
as_desktop bash -c 'cd "$1" && ./install.sh --alternative' _ "$SOURCE_DIR/icons"
as_desktop bash -c 'cd "$1" && ./install.sh' _ "$SOURCE_DIR/cursors"

echo "[4/6] Big Sur sounds (latest official release)"
SOUND_REPO="gxanshu/macos-bigsur-sound-theme-linux"
SOUND_URL="$(curl -fsSL --retry 3 "https://api.github.com/repos/$SOUND_REPO/releases/latest" |
  jq -r '.assets[] | select(.name | test("\\.tar\\.gz$")) | .browser_download_url' | head -n1)"
if [[ -n "$SOUND_URL" ]]; then
  curl -fsSL --retry 3 -o "$SOURCE_DIR/sounds.tar.gz" "$SOUND_URL"
  mkdir -p "$SOURCE_DIR/sounds"
  tar -xzf "$SOURCE_DIR/sounds.tar.gz" -C "$SOURCE_DIR/sounds" --no-same-owner
  THEME_INDEX="$(find "$SOURCE_DIR/sounds" -type f -path '*/bigsur/index.theme' -print -quit)"
  if [[ -n "$THEME_INDEX" ]]; then
    as_desktop mkdir -p "$TARGET_HOME/.local/share/sounds"
    mkdir -p "$TARGET_HOME/.local/share/sounds/bigsur"
    cp -a "$(dirname "$THEME_INDEX")/." "$TARGET_HOME/.local/share/sounds/bigsur/"
    chown -R "$TARGET_USER:$TARGET_USER" "$TARGET_HOME/.local/share/sounds/bigsur"
  else
    echo "Sound archive downloaded but theme folder not found; continuing."
  fi
else
  echo "Latest Big Sur sound release has no tar.gz asset; continuing without sounds."
fi

echo "[5/6] Apply colors, icons, cursor and sound preferences (no SDDM changes)"
KWRITE="$(command -v kwriteconfig6 || command -v kwriteconfig5 || true)"
if [[ -n "$KWRITE" ]]; then
  as_desktop "$KWRITE" --file kdeglobals --group Icons --key Theme WhiteSur
  as_desktop "$KWRITE" --file kcminputrc --group Mouse --key cursorTheme WhiteSur-cursors
  as_desktop "$KWRITE" --file kcminputrc --group Mouse --key cursorSize 32
  if [[ -d "$TARGET_HOME/.local/share/sounds/bigsur" ]]; then
    as_desktop "$KWRITE" --file kdeglobals --group Sounds --key Theme bigsur
  fi
  as_desktop "$KWRITE" --file plasmarc --group Theme --key name WhiteSur-dark
  if fc-match -f '%{family}' Inter 2>/dev/null | grep -qi '^Inter'; then
    as_desktop "$KWRITE" --file kdeglobals --group General --key font 'Inter,10,-1,5,50,0,0,0,0,0'
  fi
else
  echo "kwriteconfig was not found. Select themes in KDE System Settings."
fi

echo "[6/6] Create an optional, safe in-session dock + menubar + wallpaper command"
cat > "$TARGET_HOME/MacOS-layout.sh" <<'LAYOUT'
#!/usr/bin/env bash
# Run inside a KDE Plasma session from Konsole as user desktop.
set -Eeuo pipefail
[[ "$USER" == desktop ]] || { echo 'Run this as desktop, not root.'; exit 1; }
command -v plasmashell >/dev/null || { echo 'Plasma is not installed.'; exit 1; }
QDBUS="$(command -v qdbus6 || command -v qdbus-qt6 || command -v qdbus || true)"
if [[ -z "$QDBUS" ]]; then
  echo 'No qdbus executable; adjust panels in KDE panel edit mode.'
  exit 1
fi
# Layout modifications are idempotent: add a dock only when none exists.
JS=$(cat <<'PLASMA_JS'
var ps = panels();
var top = null;
var bottom = null;
for (var i = 0; i < ps.length; i++) {
  if (ps[i].location === "top") top = ps[i];
  if (ps[i].location === "bottom") bottom = ps[i];
}
if (!top && bottom) {
  top = bottom;
  top.location = "top";
  top.height = 34;
  top.hiding = "none";
  bottom = null;
}
if (!top) {
  top = new Panel;
  top.location = "top";
  top.height = 34;
  top.addWidget("org.kde.plasma.kickoff");
  top.addWidget("org.kde.plasma.systemtray");
  top.addWidget("org.kde.plasma.digitalclock");
}
if (!bottom) {
  var dock = new Panel;
  dock.location = "bottom";
  dock.height = 58;
  dock.alignment = "center";
  dock.lengthMode = "fit";
  dock.hiding = "autohide";
  dock.addWidget("org.kde.plasma.icontasks");
}
PLASMA_JS
)
"$QDBUS" org.kde.plasmashell /PlasmaShell org.kde.PlasmaShell.evaluateScript "$JS"
if command -v plasma-apply-wallpaperimage >/dev/null 2>&1; then
  WALLPAPER="$(find "$HOME/.local/share/wallpapers" -type f \( -iname '*.png' -o -iname '*.jpg' -o -iname '*.jpeg' \) 2>/dev/null | head -n1 || true)"
  if [[ -n "$WALLPAPER" ]]; then
    plasma-apply-wallpaperimage "$WALLPAPER" || true
  fi
fi
echo 'Mac-style top panel, bottom dock and wallpaper applied.'
LAYOUT
chown "$TARGET_USER:$TARGET_USER" "$TARGET_HOME/MacOS-layout.sh"
chmod 755 "$TARGET_HOME/MacOS-layout.sh"

echo
echo "INSTALL COMPLETE for $EXPECTED_HOST."
echo "WhiteSur KDE, icons, cursors, wallpaper package and (if available) Big Sur sounds are installed."
echo "Log out and back into the desktop via XRDP."
echo "Then open KDE Konsole as 'desktop' and run: bash ~/MacOS-layout.sh"
echo "XRDP, SDDM and your XFCE fallback were not modified."

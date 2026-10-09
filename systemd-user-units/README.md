# systemd user units (reference copies)

These are reference copies of systemd --user unit files running on the VPS
(`~/.config/systemd/user/` on vps-bb300bba), kept here for history/review.
They are not installed from this repo automatically.

## chatgpt-firefox.service

2026-10-09: fixed a bug where Firefox for the ChatGPT project runner always
launched on the headless `Xvfb :99` virtual display (via
`chatgpt-display.service`/`chatgpt-openbox.service`), making it invisible
even though the VPS has a real, persistent, xrdp-visible X session on `:10`
(`Xorg :10`, spawned by `xrdp-sesexec`, survives RDP disconnects). Per the
project requirement that Firefox must run visibly/interactively on the
remote-desktop display (for Violentmonkey-based workers), the unit now
targets `DISPLAY=:10` with `XAUTHORITY=/home/ubuntu/.Xauthority`, and no
longer requires/depends on the headless display/openbox units (the real
`:10` session already has its own window manager via the desktop
environment started by xrdp/sddm).

`local-bin/chatgpt-window-layout.sh` (the `ExecStartPost` window-tiling
script) was updated the same way, since it hardcoded `DISPLAY=:99` too.

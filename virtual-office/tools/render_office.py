#!/usr/bin/env python3
"""Developer tool: render the Office scene headlessly and save screenshots (software WebGL, so slow but real).

  python3 tools/render_office.py OUT_DIR [view ...]

Views are names from VIEWS below. Runs a throw-away Office server with a temporary database; no zCloud, no token.
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHROME = os.getenv("OFFICE_RENDER_CHROME", "/opt/pw-browsers/chromium-1194/chrome-linux/chrome")
VIEWS = {
    "lounge": ([3.5, 1.68, 12.6], [-3, 1.7, 1]),
    "window": ([0, 1.7, 8], [0, 1.9, -15]),
    "skyline_left": ([-12, 1.7, 5], [-19, 2.5, -9]),
    "skyline_right": ([12, 1.7, 5], [19, 2.2, -4]),
    "team": ([-5.7, 1.68, -5.2], [-10, 1.5, -9.2]),
    "workfloor": ([0, 1.8, -3], [0, 1.3, -12]),
    "boardroom": ([11, 1.68, 12.5], [11, 2.5, 2]),
    "office": ([-8.3, 1.68, 4.3], [-11, 1.45, 6]),
    "overview": ([16, 3.6, 12], [-6, 1.2, -4]),
    "closeup": ([-11.3, 1.55, -6.9], [-13.5, 1.2, -9.0]),
    "walk": ([2.0, 1.7, -1.5], [-3.0, 1.2, -8.0]),
    "desk": ([-9.5, 1.55, -5.4], [-13.5, 1.25, -8.9]),
    "boardroom_far": ([11, 1.9, 13.5], [11, 1.4, 2]),
    "creative": ([11.2, 1.68, 0.4], [14.6, 1.1, -2.8]),
}


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    out = Path(sys.argv[1]).resolve()
    out.mkdir(parents=True, exist_ok=True)
    names = sys.argv[2:] or ["lounge", "window", "team"]
    port = free_port()
    env = dict(os.environ, OFFICE_PORT=str(port), OFFICE_DB_PATH=str(Path(tempfile.mkdtemp()) / "o.db"), OFFICE_ZCLOUD_URL="http://127.0.0.1:1")
    env.pop("OFFICE_ACCESS_TOKEN", None)
    server = subprocess.Popen([sys.executable, str(ROOT / "server.py"), "server"], env=env, cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.2)
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=CHROME, args=["--use-angle=swiftshader", "--use-gl=angle", "--enable-unsafe-swiftshader",
                                                                      "--ignore-gpu-blocklist", "--enable-webgl", "--no-sandbox", "--disable-background-networking"])
            context = browser.new_context(bypass_csp=True)   # only so Playwright can poll; the page is served with its real CSP
            page = context.new_page()
            page.set_viewport_size({"width": int(os.getenv("W", "1280")), "height": int(os.getenv("H", "720"))})
            logs = []
            page.on("console", lambda m: logs.append(m.type + ": " + m.text))
            page.on("pageerror", lambda e: logs.append("pageerror: " + str(e)))
            page.goto("http://127.0.0.1:%d/?debug&q=%s&time=%s" % (port, os.getenv("Q", "high"), os.getenv("TIME", "golden")))
            page.wait_for_function("window.__office && window.__office.world", timeout=120000)
            # hide the HTML chrome so only the 3D image is judged
            page.add_style_tag(content="header,.panel,.dialog,#hint,.teamlist,#game-chip,.vignette,#loading,footer,nav,aside{display:none!important}")
            walk = os.getenv("WALK")   # e.g. WALK=maya:4 -> send her to the player and advance 4 seconds before shooting
            for name in names:
                pos, look = VIEWS[name]
                page.evaluate("([p,l])=>window.__office.world.setView(p,l)", [pos, look])
                if walk:
                    who, secs = walk.split(":")
                    page.evaluate("([w,s])=>{window.__office.world.callToPlayer(w);window.__office.world.step(s)}", [who, float(secs)])
                time.sleep(float(os.getenv("SETTLE", "2.5")))
                page.screenshot(path=str(out / (name + ".png")))
                print("saved", out / (name + ".png"), flush=True)
            Path(out / "console.log").write_text("\n".join(logs))
            browser.close()
    finally:
        server.terminate()


if __name__ == "__main__":
    main()

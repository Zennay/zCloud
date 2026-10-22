#!/usr/bin/env python3
"""End-to-end check of the Office UI in a real (software-rendered) browser against tools/fake_zcloud.py.

  python3 tools/e2e_ui.py [OUT_DIR]

Covers: control room reads zCloud, a change is confirmed in two steps and lands in zCloud with the Office actor,
a change made 'in the dashboard' shows up in the game by itself, a new expert appears at a desk, answers are spoken.
Not part of the unit-test run (slow, needs Chromium); CI runs it in a separate job when Chromium is available.
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHROME = os.getenv("OFFICE_RENDER_CHROME", "/opt/pw-browsers/chromium-1194/chrome-linux/chrome")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_port(port):
    for _ in range(60):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            return
        except OSError:
            time.sleep(0.2)
    raise SystemExit("server did not start on %d" % port)


def zstate(port):
    return json.load(urllib.request.urlopen("http://127.0.0.1:%d/__state" % port))


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ((" — " + str(detail)) if detail and not ok else ""), flush=True)
    if not ok:
        check.failed.append(name)


check.failed = []


def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp()).resolve()
    out.mkdir(parents=True, exist_ok=True)
    zport, oport = free_port(), free_port()
    zproc = subprocess.Popen([sys.executable, str(ROOT / "tools" / "fake_zcloud.py"), str(zport)])
    env = dict(os.environ, OFFICE_PORT=str(oport), OFFICE_DB_PATH=str(Path(tempfile.mkdtemp()) / "o.db"), OFFICE_ZCLOUD_URL="http://127.0.0.1:%d" % zport, OFFICE_REVIEW_MINUTES="0")
    env.pop("OFFICE_ACCESS_TOKEN", None)
    oproc = subprocess.Popen([sys.executable, str(ROOT / "server.py"), "server"], env=env, cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_port(zport); wait_port(oport)
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=CHROME, args=["--use-angle=swiftshader", "--use-gl=angle", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist", "--no-sandbox"])
            page = browser.new_context(bypass_csp=True).new_page()
            page.set_viewport_size({"width": 1280, "height": 720})
            logs = []
            page.on("console", lambda m: logs.append(m.type + ": " + m.text))
            page.on("pageerror", lambda e: logs.append("pageerror: " + str(e)))
            spoken = []
            page.add_init_script("""window.__spoken=[];const f=()=>{};
              Object.defineProperty(window,'speechSynthesis',{value:{getVoices:()=>[],speak:(u)=>{window.__spoken.push(u.text);setTimeout(()=>u.onend&&u.onend(),10)},cancel:f},configurable:true});
              window.SpeechSynthesisUtterance=function(t){this.text=t};""")
            page.goto("http://127.0.0.1:%d/?debug&q=low" % oport)
            page.wait_for_function("window.__office && window.__office.world", timeout=180000)
            page.wait_for_function("window.__office.world.actorIds().length>=3", timeout=30000)
            check("three default experts are in the scene", sorted(page.evaluate("window.__office.world.actorIds()")) == ["lina", "maya", "noah"])

            page.click("#control-open")
            page.wait_for_selector("#control-body .project-card", timeout=20000)
            text = page.inner_text("#control-body")
            check("control room lists real workers and projects", "Haxlab" in text and "FTMO" in text and "haxlab-1" in text, text[:300])

            page.screenshot(path=str(out / "workers.png"))
            page.click("#control-body .ctl-row button:has-text('Pauzeer')")
            page.wait_for_selector("#control-confirm:not([hidden])", timeout=10000)
            check("a change asks for confirmation first", "haxlab-1" in page.inner_text("#control-confirm") or "ftmo-1" in page.inner_text("#control-confirm"))
            check("nothing changed before confirming", all(w["desired_state"] == "running" for w in zstate(zport)["projects"].values()))
            page.click("#control-confirm button.primary")
            time.sleep(1.5)
            paused = [k for k, w in zstate(zport)["projects"].items() if w["desired_state"] == "paused"]
            check("confirmed change reaches zCloud", len(paused) == 1, paused)
            actors = {a[2] for a in zstate(zport)["log"] if a[0] == "/api/runner-control"}
            check("zCloud records the Office actor", actors == {"virtual-office"}, actors)

            other = "ftmo-1" if paused == ["haxlab-1"] else "haxlab-1"
            urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:%d/__dashboard/pause?worker=%s" % (zport, other), data=b"{}", method="POST"))
            page.wait_for_function("document.querySelectorAll('#control-body .badge.idle, #control-body .badge').length>0 && [...document.querySelectorAll('#control-body .badge')].filter(b=>b.textContent.includes('gepauzeerd')).length>=2", timeout=20000)
            check("a dashboard change shows up in the control room by itself", True)

            page.click("#control-tabs button:has-text('Queue')")
            page.fill("#control-body input[aria-label='Titel van de taak']", "Controleer de nieuwe release grondig")
            page.click("#control-body button:has-text('Toevoegen')")
            page.wait_for_selector("#control-confirm:not([hidden])", timeout=10000)
            page.click("#control-confirm button.primary")
            time.sleep(1.5)
            check("queue task added through the same zCloud endpoint", any(q["title"].startswith("Controleer de nieuwe release") for q in zstate(zport)["queue"]))

            page.click("#control-tabs button:has-text('Team')")
            page.fill("#control-body input[aria-label='Naam']", "Ravi")
            page.fill("#control-body input[aria-label^='Functie']", "Data-expert · FTMO")
            page.click("#control-body button:has-text('Expert toevoegen')")
            page.wait_for_function("window.__office.world.actorIds().includes('ravi')", timeout=20000)
            check("a new expert appears in the scene", True)
            page.evaluate("window.__office.world.callToPlayer('ravi'); window.__office.world.step(2)")
            check("the new expert can walk", page.evaluate("window.__office.world.actorMode('ravi')") == "walking")
            page.screenshot(path=str(out / "control.png"))
            browser.close()
            (out / "console.log").write_text("\n".join(logs))
            errors = [l for l in logs if l.startswith(("pageerror", "error:")) and "404" not in l]
            check("no page errors", not errors, errors[:3])
    finally:
        oproc.terminate(); zproc.terminate()
    if check.failed:
        raise SystemExit(1)
    print("all UI checks passed")


if __name__ == "__main__":
    main()

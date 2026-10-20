#!/usr/bin/env python3
"""One-shot, non-disruptive activation of the already-installed zCloud v2 addon.

Runs as ubuntu from a user-level systemd timer. Never changes queue/SQLite,
Firefox windows, tabs, or worker state. Fails closed unless the same workers
are independently observed idle across a bounded interval.
"""
from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

API = "http://127.0.0.1:8765/api/runner-live"
HOME = Path("/home/ubuntu")
CONTROLLER = HOME / "snap/firefox/common/chatgpt-project-extension/worker-controller-v2.js"
SCRIPT = HOME / "zennay-cloud/public/zcloud-worker.user.js"
RELOADER = HOME / ".local/libexec/zcloud/zcloud_reload_extension.mjs"
STATE = HOME / ".local/state/zcloud"
DONE = STATE / "force-v2-2.0.4-addon-activated"
LOCK = STATE / "force-v2-2.0.4-addon-activation.lock"
TIMER = "zcloud-force-v2-idle-activation.timer"


def safe_snapshot(payload):
    runners = payload.get("chatgpt_runners") if isinstance(payload, dict) else None
    if not isinstance(runners, dict) or not runners:
        raise ValueError("missing-runners")
    identities = set()
    for project in runners.values():
        if not isinstance(project, dict) or not isinstance(project.get("workers"), list):
            raise ValueError("invalid-project-workers")
        for worker in project["workers"]:
            if not isinstance(worker, dict):
                raise ValueError("invalid-worker")
            if worker.get("active") is not True:
                continue
            wid = worker.get("worker_id")
            if not isinstance(wid, str) or not wid or wid in identities:
                raise ValueError("duplicate-or-missing-worker-identity")
            identities.add(wid)
            if worker.get("generating") is not False or worker.get("sending") is not False:
                raise ValueError("generating-sending-or-unverified")
            if worker.get("desired_state") == "draining":
                raise ValueError("draining")
            command = worker.get("command")
            if isinstance(command, dict) and command.get("status") == "pending":
                raise ValueError("pending-command")
            event = worker.get("last_event")
            age = worker.get("age_seconds")
            if isinstance(event, dict) and event.get("event") in ("prompt-sent", "generation-started"):
                if not isinstance(age, (int, float)) or isinstance(age, bool) or age < 180:
                    raise ValueError("unconfirmed-recent-generation")
    if not identities:
        raise ValueError("no-active-workers")
    return frozenset(identities)


def read_snapshot():
    request = urllib.request.Request(API, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=8) as response:
        if response.status != 200 or response.headers.get_content_type() != "application/json":
            raise ValueError("invalid-runner-api-response")
        data = response.read(262145)
    if len(data) > 262144:
        raise ValueError("oversized-runner-response")
    return safe_snapshot(json.loads(data))


def installed():
    if os.geteuid() != 1000 or not all(p.is_file() and not p.is_symlink()
                                         for p in (CONTROLLER, SCRIPT, RELOADER)):
        return False
    controller = CONTROLLER.read_text(encoding="utf-8")
    userscript = SCRIPT.read_text(encoding="utf-8")
    return ("function forceNewWorkerTab(" in controller and
            'x.action === "force_start"' in controller and
            "// @version      2.0.4" in userscript and
            '"force_start"' in userscript and
            'data-zcloud-v2-busy' in userscript)


def main():
    STATE.mkdir(parents=True, exist_ok=True)
    with LOCK.open("a+") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("ACTIVATION_DEFERRED=already-checking")
            return 0
        if DONE.exists():
            print("ACTIVATION_ALREADY_COMPLETED=1")
            return 0
        if not installed():
            print("ACTIVATION_DEFERRED=source-not-installed-or-identity-untrusted")
            return 0
        try:
            one = read_snapshot()
            time.sleep(10)
            two = read_snapshot()
            if one != two:
                raise ValueError("worker-set-changed")
            # Final independent check immediately before the add-on reload.
            three = read_snapshot()
            if two != three:
                raise ValueError("worker-set-changed-before-reload")
        except Exception as error:
            print("ACTIVATION_DEFERRED=" + type(error).__name__ + ":" + str(error)[:120])
            return 0
        try:
            subprocess.run(["node", str(RELOADER)], check=True, timeout=30,
                           cwd=str(HOME), env={**os.environ, "HOME": str(HOME)})
            DONE.write_text(dt.datetime.now(dt.timezone.utc).isoformat() + "\n")
            print("FORCE_V2_ADDON_ACTIVATED_WITHOUT_FIREFOX_RESTART=1")
            subprocess.run(["systemctl", "--user", "disable", "--now", TIMER],
                           check=False, timeout=15)
            return 0
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as error:
            print("ACTIVATION_RETRY=" + type(error).__name__)
            return 0


if __name__ == "__main__":
    sys.exit(main())

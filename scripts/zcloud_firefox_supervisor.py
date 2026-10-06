#!/usr/bin/env python3
"""Tiny root-side liveness guard for the standalone zCloud Firefox runtime.

The material-progress watchdog handles semantic worker failures. This supervisor
only protects the browser host itself from two infrastructure failures that must
recover quickly:
1. allocated workers exist but the Firefox process disappeared;
2. standalone Firefox inherited the obsolete Nice=10 policy and is starved under
   heavy compute load.

It never writes history.db. Browser restart goes through zCloud's existing local
runner-control API so conversation/allocation recovery remains centralized.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DB = Path(os.environ.get("ZCLOUD_DB", "/home/ubuntu/zennay-cloud/history.db"))
STATE = Path(os.environ.get("ZCLOUD_FIREFOX_SUPERVISOR_STATE", "/run/zcloud-firefox-supervisor.json"))
BASE_URL = os.environ.get("ZCLOUD_BASE_URL", "http://127.0.0.1:8765")
FIREFOX_UID = int(os.environ.get("ZCLOUD_FIREFOX_UID", "1000"))
MISSING_CONFIRMATIONS = max(1, int(os.environ.get("ZCLOUD_FIREFOX_MISSING_CONFIRMATIONS", "2")))
RESTART_COOLDOWN_SECONDS = max(10, int(os.environ.get("ZCLOUD_FIREFOX_RESTART_COOLDOWN_SECONDS", "30")))
TARGET_NICE = int(os.environ.get("ZCLOUD_FIREFOX_TARGET_NICE", "0"))


def load_state() -> dict[str, Any]:
    try:
        value = json.loads(STATE.read_text())
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def save_state(value: dict[str, Any]) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    temp = STATE.with_suffix(".tmp")
    temp.write_text(json.dumps(value, sort_keys=True) + "\n")
    temp.replace(STATE)


def allocated_workers(db_path: Path = DB) -> int:
    if not db_path.exists():
        return 0
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)
        try:
            return int(conn.execute("SELECT COUNT(*) FROM ai_global_slots").fetchone()[0])
        finally:
            conn.close()
    except Exception:
        return 0


def firefox_pids(uid: int = FIREFOX_UID) -> list[int]:
    proc = subprocess.run(
        ["pgrep", "-u", str(uid), "-f", r"/snap/firefox/.*/usr/lib/firefox/firefox"],
        text=True,
        capture_output=True,
        check=False,
        timeout=3,
    )
    return [int(item) for item in proc.stdout.split() if item.isdigit()]


def normalize_priority(pids: list[int], target_nice: int = TARGET_NICE) -> dict[str, Any]:
    changed: list[int] = []
    observed: dict[str, int] = {}
    errors: dict[str, str] = {}
    for pid in pids:
        try:
            current = os.getpriority(os.PRIO_PROCESS, pid)
            observed[str(pid)] = int(current)
            if current > target_nice:
                os.setpriority(os.PRIO_PROCESS, pid, target_nice)
                changed.append(pid)
        except ProcessLookupError:
            continue
        except Exception as exc:
            errors[str(pid)] = f"{type(exc).__name__}: {exc}"[:240]
    return {"target_nice": target_nice, "observed": observed, "changed": changed, "errors": errors}


def restart_firefox(base_url: str = BASE_URL) -> dict[str, Any]:
    payload = json.dumps({"project_id": "", "action": "restart_firefox"}).encode("utf-8")
    request = urllib.request.Request(
        base_url.rstrip("/") + "/api/runner-control",
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "zcloud-firefox-supervisor/1"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            body = json.load(response)
            return {"ok": int(response.status) < 300 and bool(body.get("ok")), "status": int(response.status), "body": body}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        return {"ok": False, "status": int(exc.code), "body": {"error": raw[:800]}}
    except Exception as exc:
        return {"ok": False, "status": 0, "body": {"error": f"{type(exc).__name__}: {exc}"[:800]}}


def run_once(*, now: float | None = None) -> dict[str, Any]:
    now = time.time() if now is None else float(now)
    state = load_state()
    allocated = allocated_workers()
    pids = firefox_pids()

    result: dict[str, Any] = {
        "ok": True,
        "allocated_workers": allocated,
        "firefox_pids": pids,
        "missing_confirmations": int(state.get("missing_confirmations") or 0),
        "priority": None,
        "restart": None,
    }

    if allocated <= 0:
        state["missing_confirmations"] = 0
        save_state(state)
        result["state"] = "idle-no-allocations"
        return result

    if pids:
        state["missing_confirmations"] = 0
        priority = normalize_priority(pids)
        result["priority"] = priority
        result["state"] = "firefox-live"
        save_state(state)
        return result

    confirmations = int(state.get("missing_confirmations") or 0) + 1
    state["missing_confirmations"] = confirmations
    result["missing_confirmations"] = confirmations
    result["state"] = "firefox-missing-confirming"

    last_restart = float(state.get("last_restart_at") or 0)
    if confirmations >= MISSING_CONFIRMATIONS and now - last_restart >= RESTART_COOLDOWN_SECONDS:
        restart = restart_firefox()
        result["restart"] = restart
        result["state"] = "restart-requested" if restart.get("ok") else "restart-request-failed"
        if restart.get("ok"):
            state["last_restart_at"] = now
            state["missing_confirmations"] = 0

    save_state(state)
    return result


def main() -> int:
    result = run_once()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    # Timer retries transient failures. Keep the service green so one API hiccup
    # never disables the liveness guard through systemd start-limit semantics.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

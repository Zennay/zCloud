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
MANAGED_RESTART_GRACE_SECONDS = max(15, int(os.environ.get("ZCLOUD_FIREFOX_MANAGED_RESTART_GRACE_SECONDS", "30")))
TARGET_NICE = int(os.environ.get("ZCLOUD_FIREFOX_TARGET_NICE", "0"))
FIREFOX_HOME = Path(os.environ.get("ZCLOUD_FIREFOX_HOME", "/home/ubuntu"))
LEGACY_DISABLE_FILE = FIREFOX_HOME / ".config/systemd/user/chatgpt-firefox.service.d/10-legacy-disabled.conf"


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


def allocated_workers_from_db(db_path: Path = DB) -> tuple[int | None, str | None]:
    if not db_path.exists():
        return None, "history.db missing"
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)
        try:
            return int(conn.execute("SELECT COUNT(*) FROM ai_global_slots").fetchone()[0]), None
        finally:
            conn.close()
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"[:300]


def allocated_workers_from_api(base_url: str = BASE_URL) -> tuple[int | None, str | None]:
    request = urllib.request.Request(
        base_url.rstrip("/") + "/api/runner-targets",
        headers={"User-Agent": "zcloud-firefox-supervisor/1"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            body = json.load(response)
        workers = ((body.get("global_allocation") or {}).get("workers") or [])
        return len(workers), None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"[:300]


def allocation_state() -> tuple[int | None, str, str | None]:
    count, error = allocated_workers_from_db()
    if count is not None:
        return count, "sqlite", None
    api_count, api_error = allocated_workers_from_api()
    if api_count is not None:
        return api_count, "api", error
    combined = "; ".join(part for part in (error, api_error) if part)
    return None, "unavailable", combined[:600] or "allocation state unavailable"


def violentmonkey_only_mode(marker: Path = LEGACY_DISABLE_FILE) -> bool:
    try:
        text = marker.read_text(encoding="utf-8").lower()
    except OSError:
        return False
    return "violentmonkey only" in text and "execcondition=/bin/false" in text


def restart_owner() -> str:
    # The historical managed unit has Restart=always/RestartSec=5. Avoid racing
    # that relaunch. Only the explicit Violentmonkey-only marker grants the fast
    # supervisor direct restart ownership.
    return "supervisor" if violentmonkey_only_mode() else "systemd"


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
    allocated, allocation_source, allocation_error = allocation_state()
    pids = firefox_pids()
    owner = restart_owner()

    result: dict[str, Any] = {
        "ok": allocated is not None,
        "allocated_workers": allocated,
        "allocation_source": allocation_source,
        "allocation_error": allocation_error,
        "firefox_pids": pids,
        "restart_owner": owner,
        "missing_confirmations": int(state.get("missing_confirmations") or 0),
        "priority": None,
        "restart": None,
    }

    if allocated is None:
        result["state"] = "allocation-state-unavailable"
        save_state(state)
        return result

    if allocated <= 0:
        state["missing_confirmations"] = 0
        state.pop("managed_missing_since", None)
        save_state(state)
        result["state"] = "idle-no-allocations"
        return result

    if pids:
        state["missing_confirmations"] = 0
        state.pop("managed_missing_since", None)
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
    restart_due = False
    if owner == "supervisor":
        # Standalone/Violentmonkey-only mode has no systemd browser owner.
        restart_due = confirmations >= MISSING_CONFIRMATIONS
    else:
        # Managed mode already has Restart=always/RestartSec=5. Give it a wide
        # enough grace to avoid a second recycle during start-post/profile load.
        missing_since = float(state.get("managed_missing_since") or now)
        state.setdefault("managed_missing_since", missing_since)
        missing_age = max(0.0, now - missing_since)
        result["managed_missing_seconds"] = round(missing_age, 3)
        result["managed_restart_grace_seconds"] = MANAGED_RESTART_GRACE_SECONDS
        result["state"] = "managed-firefox-restart-grace"
        restart_due = missing_age >= MANAGED_RESTART_GRACE_SECONDS

    if restart_due and now - last_restart >= RESTART_COOLDOWN_SECONDS:
        restart = restart_firefox()
        result["restart"] = restart
        result["state"] = "restart-requested" if restart.get("ok") else "restart-request-failed"
        if restart.get("ok"):
            state["last_restart_at"] = now
            state["missing_confirmations"] = 0
            state.pop("managed_missing_since", None)

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

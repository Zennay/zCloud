#!/usr/bin/env python3
"""Activate the ten-worker zCloud fleet on the NEW VPS only.

The existing apply_worker_fleet.py owns SQLite desired-state writes. This
operator entrypoint checks the migrated browser/session first, makes an
online SQLite backup, invokes that existing writer, starts the already-installed
services without restarting live browsers, and requires fresh per-slot events.
No Firefox profile, login, extension or systemd unit is silently fabricated.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
import pwd
from pathlib import Path
import socket
import sqlite3
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request

HOST = "VM-7eb5ee16-2c71-440d-b7fd-8095d204e930"
ROOT = Path("/home/ubuntu/zennay-cloud")
PROFILE = Path("/home/ubuntu/snap/firefox/common/chatgpt-webext-profile")
SESSION_SOCKET = Path("/tmp/.X11-unix/X10")
API = "http://127.0.0.1:8765"
# A prompt event proves activity, not that the browser remains connected.
APPROVED_EVENTS = {"heartbeat"}


class Refused(RuntimeError):
    pass


def command(*args: str, env: dict | None = None, timeout: int = 30) -> str:
    try:
        proc = subprocess.run(args, env=env, text=True, capture_output=True,
                              timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Refused(f"command unavailable: {args[0]} ({type(exc).__name__})") from exc
    if proc.returncode:
        # systemctl/CLI output may contain private state: don't echo stderr.
        raise Refused(f"{args[0]} failed with exit code {proc.returncode}")
    return proc.stdout.strip()


def system_state(unit: str, *, user: bool = False, env: dict | None = None) -> str:
    args = ["systemctl"]
    if user:
        args.append("--user")
    return command(*args, "show", unit, "-p", "LoadState", "--value", env=env)


def is_active(unit: str, *, user: bool = False, env: dict | None = None) -> bool:
    args = ["systemctl"]
    if user:
        args.append("--user")
    return subprocess.run([*args, "is-active", "--quiet", unit],
                          env=env, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, timeout=10).returncode == 0


def api(method: str, path: str, payload: dict | None = None) -> dict:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        API + path, data=body, method=method,
        headers={"Accept": "application/json", "Content-Type": "application/json",
                 "User-Agent": "zcloud-new-vps-worker-activation/1"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=8) as resp:
            if resp.status != 200:
                raise Refused(f"{path}: unexpected HTTP status {resp.status}")
            raw = resp.read(131073)
            if len(raw) > 131072:
                raise Refused(f"{path}: oversized API response")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise Refused(f"{path}: invalid JSON response")
            return data
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise Refused(f"{path}: unavailable or invalid response") from exc


def timestamp(raw: object) -> datetime | None:
    try:
        value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return value.astimezone(timezone.utc) if value.tzinfo else None
    except (ValueError, TypeError, OverflowError):
        return None


def fresh_events(keys: list[tuple[str, int]], since: datetime) -> list[str]:
    present = []
    with sqlite3.connect(f"file:{ROOT / 'history.db'}?mode=ro", uri=True,
                         timeout=5) as conn:
        for project, slot in keys:
            rows = conn.execute(
                "SELECT ts,event FROM runner_events "
                "WHERE project_id=? AND worker_slot=? ORDER BY id DESC LIMIT 40",
                (project, slot),
            ).fetchall()
            if any(event in APPROVED_EVENTS and (stamp := timestamp(ts))
                   and since <= stamp <= datetime.now(timezone.utc)
                   for ts, event in rows):
                present.append(f"{project}::w{slot}")
    return present


def _ensure_private_backup_directory() -> Path:
    home = Path("/home/ubuntu")
    try:
        home_info = home.lstat()
    except OSError as exc:
        raise Refused(f"ubuntu home unavailable ({type(exc).__name__})") from None
    if not stat.S_ISDIR(home_info.st_mode) or home_info.st_uid != os.getuid():
        raise Refused("backup home must be a real ubuntu-owned directory")

    dest = home
    for part in (".local", "state", "zcloud", "fleet-backups"):
        dest = dest / part
        try:
            info = dest.lstat()
        except FileNotFoundError:
            try:
                dest.mkdir(mode=0o700, exist_ok=False)
            except FileExistsError:
                pass
            except OSError as exc:
                raise Refused(f"backup directory unavailable ({type(exc).__name__})") from None
            try:
                info = dest.lstat()
            except OSError as exc:
                raise Refused(f"backup directory unavailable ({type(exc).__name__})") from None
        except OSError as exc:
            raise Refused(f"backup directory unavailable ({type(exc).__name__})") from None
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            raise Refused("backup path components must be real ubuntu-owned directories")

    fd = None
    try:
        fd = os.open(
            dest,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
        )
        os.fchmod(fd, 0o700)
    except OSError as exc:
        raise Refused(f"backup directory could not be secured ({type(exc).__name__})") from None
    finally:
        if fd is not None:
            os.close(fd)
    return dest


def backup() -> Path:
    dest = _ensure_private_backup_directory()
    old_umask = os.umask(0o077)
    target: Path | None = None
    backup_fd: int | None = None
    target_identity: tuple[int, int] | None = None
    try:
        # Timestamp precision is not the safety boundary: O_EXCL guarantees a
        # retry can never open and overwrite an earlier pre-fleet snapshot.
        for _ in range(5):
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            candidate = dest / f"pre-fleet-{stamp}.sqlite3"
            try:
                backup_fd = os.open(
                    candidate,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY
                    | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
                    0o600,
                )
                target = candidate
                opened = os.fstat(backup_fd)
                target_identity = (opened.st_dev, opened.st_ino)
                break
            except FileExistsError:
                continue
        if target is None or backup_fd is None or target_identity is None:
            raise Refused("could not allocate a unique backup path without overwrite")

        with sqlite3.connect(f"file:{ROOT / 'history.db'}?mode=ro", uri=True,
                             timeout=15) as source:
            with sqlite3.connect(target, timeout=15) as copy:
                source.backup(copy)

        current = target.lstat()
        if (not stat.S_ISREG(current.st_mode)
                or (current.st_dev, current.st_ino) != target_identity):
            raise Refused("backup target identity changed during creation")
        os.fchmod(backup_fd, 0o600)
        return target
    except Exception as exc:
        # Remove only the incomplete file this invocation created. Never unlink
        # an existing or replacement snapshot when the path identity changed.
        if target is not None and target_identity is not None:
            try:
                current = target.lstat()
                if (current.st_dev, current.st_ino) == target_identity:
                    target.unlink()
            except OSError:
                pass
        if isinstance(exc, Refused):
            raise
        raise Refused(
            f"SQLite backup failed ({type(exc).__name__}); existing backups were not overwritten"
        ) from None
    finally:
        if backup_fd is not None:
            os.close(backup_fd)
        os.umask(old_umask)


def browser_running() -> bool:
    # Check only this user's real Firefox process; do not kill other browsers.
    proc = subprocess.run(
        ["pgrep", "-u", str(os.getuid()), "-f",
         r"(/usr/lib/firefox/firefox|firefox-bin|/snap/firefox/)"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
    )
    return proc.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Make backup, apply desired state and start the fleet")
    parser.add_argument("--wait-seconds", type=int, default=120)
    args = parser.parse_args()
    if args.wait_seconds < 15 or args.wait_seconds > 300:
        raise Refused("wait-seconds must be in [15,300]")
    if socket.gethostname() != HOST:
        raise Refused("wrong hostname; old VPS and cloned runners must remain untouched")
    try:
        ubuntu_uid = pwd.getpwnam("ubuntu").pw_uid
    except KeyError as exc:
        raise Refused("new VPS ubuntu account is missing") from exc
    if ubuntu_uid == 0 or os.geteuid() != ubuntu_uid or os.getuid() != ubuntu_uid:
        raise Refused(
            f"run as the new VPS ubuntu user (UID {ubuntu_uid}), "
            f"not uid={os.getuid()} euid={os.geteuid()}; "
            "from root, use: runuser -u ubuntu -- env "
            "HOME=/home/ubuntu XDG_RUNTIME_DIR=/run/user/$(id -u ubuntu) "
            "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u ubuntu)/bus "
            "python3 /home/ubuntu/zennay-cloud/ops/activate-worker-engine-new-vps-20261022.py"
        )
    if ROOT.resolve() != Path(__file__).resolve().parent.parent:
        raise Refused("script is not running from canonical /home/ubuntu/zennay-cloud")
    plan = json.loads((ROOT / "worker-fleet.json").read_text(encoding="utf-8"))
    if plan.get("target_hostname") != HOST or plan.get("active_workers") != 10:
        raise Refused("fleet plan does not match intended host and ten active slots")
    if plan.get("providers") != {"chatgpt": 10, "claude": 0}:
        raise Refused("unexpected provider allocation")
    expected = [(project, slot) for project, count in plan["projects"].items()
                for slot in range(1, count + 1)]
    if len(expected) != 10:
        raise Refused("project slots do not add up to ten")
    if not (ROOT / "history.db").is_file():
        raise Refused("migrated SQLite state missing; no empty replacement will be created")
    ext = ROOT / "firefox-extension"
    if any(not (ext / name).is_file() for name in
           ("manifest.json", "background.js", "recovery.js", "worker-controller-v2.js")):
        raise Refused("complete Firefox worker extension source is missing")
    if not (PROFILE / "prefs.js").is_file():
        raise Refused("migrated Firefox worker profile missing; refusing blank login/session")
    if not (PROFILE / "cookies.sqlite").is_file():
        raise Refused("browser login cookies missing; verify the migrated profile")
    if not SESSION_SOCKET.exists() or not Path("/home/ubuntu/.Xauthority").exists():
        raise Refused("X11 :10 interactive session/Xauthority unavailable; do not launch headless")
    env = os.environ.copy()
    runtime_dir = f"/run/user/{ubuntu_uid}"
    env.update({"XDG_RUNTIME_DIR": runtime_dir,
                "DBUS_SESSION_BUS_ADDRESS": f"unix:path={runtime_dir}/bus"})
    # Existing X11 socket/cookie files do not prove access; verify ubuntu's
    # real display before starting the managed Firefox service.
    command("xdotool", "getdisplaygeometry",
            env={**env, "DISPLAY": ":10",
                 "XAUTHORITY": "/home/ubuntu/.Xauthority"}, timeout=5)
    if system_state("chatgpt-firefox.service", user=True, env=env) != "loaded":
        raise Refused("chatgpt-firefox user service not installed")
    if system_state("zennay-cloud.service") != "loaded":
        raise Refused("zennay-cloud backend service not installed")
    for timer in ("zcloud-firefox-supervisor.timer", "zcloud-self-heal.timer"):
        if system_state(timer) != "loaded":
            raise Refused(f"{timer} not installed; stop before activating")
    # Check the current capacity, contracts, schema and nonexpired claims first.
    preflight = command(sys.executable, str(ROOT / "scripts/apply_worker_fleet.py"),
                        env=env, timeout=30)
    dry = json.loads(preflight)
    if dry.get("requested_active") != 10 or dry.get("maximum") != 20:
        raise Refused("existing fleet writer reports unexpected capacity")
    # Old main lacks the atomic queue/lease cutover gate.
    cutover_gate = dry.get("cutover_gate")
    managed = is_active("chatgpt-firefox.service", user=True, env=env)
    standalone = browser_running() and not managed
    state = {
        "hostname": HOST, "mode": "apply" if args.apply else "preflight",
        "desired": 10, "maximum": 20, "profile_present": True,
        "extension_present": True, "browser_managed_active": managed,
        "standalone_browser_present": standalone,
        "backend_active": is_active("zennay-cloud.service"),
        "cutover_gate": cutover_gate,
    }
    if standalone:
        raise Refused("standalone Firefox is live: preserve its tabs; owner must reconcile service")
    if not args.apply:
        print(json.dumps(state, sort_keys=True, indent=2))
        return 0

    if os.environ.get("ZCLOUD_OLD_VPS_FENCED") != "yes":
        raise Refused("old VPS scheduler/workers must be fenced before --apply")
    if os.environ.get("ZCLOUD_FINAL_DB_SYNCED") != "yes":
        raise Refused("final consistent SQLite handoff must be confirmed before --apply")
    if not isinstance(cutover_gate, dict) or cutover_gate.get("clear") is not True:
        raise Refused("atomic fleet cutover gate absent or blocked; integrate guarded writer first")
    if state["backend_active"]:
        raise Refused("zCloud backend already active; do not rewrite live scheduler state")

    snapshot = backup()
    state["sqlite_backup"] = str(snapshot)
    # Desired-state rewrite must precede API startup: otherwise the scheduler
    # can claim work after the preflight but before the database mutation.
    command(sys.executable, str(ROOT / "scripts/apply_worker_fleet.py"),
            "--apply", env=env, timeout=40)
    command("sudo", "-n", "systemctl", "start", "zennay-cloud.service",
            timeout=25)
    if not is_active("zennay-cloud.service"):
        raise Refused("zennay-cloud backend did not become active")
    # An already-managed browser retains its live tabs: never restart it.
    if not managed:
        command("systemctl", "--user", "enable", "--now",
                "chatgpt-firefox.service", env=env, timeout=45)
    if not is_active("chatgpt-firefox.service", user=True, env=env):
        raise Refused("managed Firefox is not active; SQLite snapshot retained")
    # Do not enable crash/self-heal timers until all ten browser sessions
    # have genuinely checked in: early watchdog recovery can destroy startup.
    start = datetime.now(timezone.utc)
    deadline = time.monotonic() + args.wait_seconds
    configured = set()
    expected_keys = {f"{project}::w{slot}" for project, slot in expected}
    while time.monotonic() < deadline:
        targets = api("GET", "/api/runner-targets")
        # Queue allocation only lists currently CLAIMED tasks. An idle queue
        # must not make ten correctly configured worker tabs look absent.
        configs = targets.get("projects") or {}
        configured = {
            key for project, slot in expected
            if (key := f"{project}::w{slot}") in configs
            and configs[key].get("base_project_id") == project
            and int(configs[key].get("worker_slot") or 0) == slot
            and configs[key].get("desired_state") == "running"
            and configs[key].get("provider") == "chatgpt"
        }
        if configured == expected_keys:
            break
        time.sleep(2)
    if configured != expected_keys:
        raise Refused(f"configured worker mismatch: expected 10, observed {len(configured)}")
    dynamic = api("GET", "/api/dynamic-workers").get("dynamic_workers") or {}
    if int(dynamic.get("chatgpt_count") or 0) != 10:
        raise Refused("runtime did not admit ten ChatGPT workers")
    if int(dynamic.get("scheduler_interval_seconds") or 0) <= 0:
        raise Refused("scheduler interval absent or disabled")
    for key in sorted(expected_keys):
        result = api("POST", "/api/runner-control",
                     {"project_id": key, "action": "start"})
        if not result.get("ok"):
            raise Refused(f"start command not accepted for {key}")
    observed = []
    while time.monotonic() < deadline:
        observed = fresh_events(expected, start)
        if set(observed) == expected_keys:
            break
        time.sleep(3)
    state["configured_slots"] = sorted(configured)
    state["fresh_heartbeat_slots"] = sorted(observed)
    state["heartbeats_verified"] = set(observed) == expected_keys
    if not state["heartbeats_verified"]:
        print(json.dumps(state, indent=2, sort_keys=True))
        raise Refused(f"only {len(observed)}/10 fresh browser heartbeats; tabs/login need inspection")
    # Verify those same per-slot heartbeat receipts through the zCloud API.
    live = api("GET", "/api/runner-live").get("chatgpt_runners") or {}
    api_heartbeats = set()
    for project, slot in expected:
        for worker in (live.get(project) or {}).get("workers") or []:
            if worker.get("worker_id") != f"{project}::w{slot}":
                continue
            last = timestamp((worker.get("last_heartbeat") or {}).get("time"))
            if last and start <= last <= datetime.now(timezone.utc):
                api_heartbeats.add(worker["worker_id"])
    state["api_heartbeat_slots"] = sorted(api_heartbeats)
    if api_heartbeats != expected_keys:
        raise Refused(f"zCloud API confirms only {len(api_heartbeats)}/10 heartbeat slots")
    allocation = api("GET", "/api/runner-targets").get("global_allocation") or {}
    state["currently_claimed_queue_slots"] = sorted(
        str(item.get("worker_key")) for item in allocation.get("workers") or []
        if item.get("worker_key")
    )
    state["queue_execution_verified"] = False
    state["queue_note"] = (
        "Ten browser slots reported heartbeats. Actual queue claim, "
        "prompt dispatch and completion require separate runtime receipts."
    )
    # Arm existing recovery timers only after browser and slot proof. Never
    # stop/restart a live browser or queue worker as part of fleet activation.
    for timer in ("zcloud-firefox-supervisor.timer", "zcloud-self-heal.timer"):
        command("sudo", "-n", "systemctl", "enable", "--now", timer, timeout=25)
        if not is_active(timer):
            raise Refused(f"{timer} failed to become active")
    state["supervisor_and_self_heal_active"] = True
    print(json.dumps(state, indent=2, sort_keys=True))
    print("ACTIVATED: ten configured ChatGPT tabs with fresh browser heartbeats; queue execution not yet proven")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (Refused, OSError, sqlite3.Error, ValueError, KeyError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        sys.exit(1)

#!/usr/bin/env python3
"""Read-only, secret-redacted display discovery for new-VPS zCloud browser workers.

No Xauthority cookies, process environment blocks, browser session data,
Firefox restarts or service mutations are emitted/performed.
"""
from __future__ import annotations

import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import sys

EXPECTED_HOST = "VM-7eb5ee16-2c71-440d-b7fd-8095d204e930"
USER = "ubuntu"
HOME = Path("/home/ubuntu")
LIMIT = 8


def cmd(argv: list[str], timeout: int = 4) -> tuple[int, str]:
    try:
        p = subprocess.run(
            argv, check=False, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return (124, "")
    return p.returncode, p.stdout.strip()


def proc_display_auth(uid: int) -> tuple[set[str], set[str]]:
    displays: set[str] = set()
    auths: set[str] = set()
    for process in Path("/proc").iterdir():
        if not process.name.isdecimal():
            continue
        try:
            if process.stat().st_uid != uid:
                continue
            env = (process / "environ").read_bytes().split(b"\0")
        except (OSError, PermissionError, ProcessLookupError):
            continue
        for item in env:
            if item.startswith(b"DISPLAY="):
                value = item.partition(b"=")[2].decode("utf-8", "replace")
                if re.fullmatch(r":[0-9]{1,3}(?:\.[0-9])?", value):
                    displays.add(value)
            if item.startswith(b"XAUTHORITY="):
                value = item.partition(b"=")[2].decode("utf-8", "replace")
                if value.startswith("/") and len(value) <= 240:
                    auths.add(value)
    return displays, auths


def permitted_auth(path: str, uid: int) -> bool:
    candidate = Path(path)
    try:
        metadata = candidate.stat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != uid:
            return False
        return candidate.is_file()
    except OSError:
        return False


def main() -> int:
    _, name = cmd(["hostname"])
    if name != EXPECTED_HOST:
        print(f"REFUSED wrong VPS hostname: {name}")
        return 2
    if os.geteuid() != 0:
        print("REFUSED: run as root to inspect session environment without copying secrets")
        return 2
    identity = pwd.getpwnam(USER)
    uid = identity.pw_uid

    print("=== Service state (no changes) ===")
    for scope, argv in (
        ("system-zCloud", ["systemctl", "show", "zennay-cloud.service"]),
        ("user-zCloud", ["systemctl", "--user", "-M", "ubuntu@", "show", "zennay-cloud.service"]),
        ("user-Firefox", ["systemctl", "--user", "-M", "ubuntu@", "show", "chatgpt-firefox.service"]),
    ):
        status_args = argv + ["-p", "ActiveState", "-p", "SubState", "-p", "Result",
                              "-p", "ExecMainStatus", "-p", "MainPID"]
        code, status = cmd(status_args)
        print(scope, status.replace("\n", " | ") if code == 0 else "not queryable")

    print("=== Logged in desktop sessions ===")
    _, sessions = cmd(["loginctl", "list-sessions", "--no-pager", "--no-legend"])
    print(sessions[:1200] if sessions else "No loginctl sessions exposed")

    print("=== X11 sockets ===")
    socket_path = Path("/tmp/.X11-unix")
    socket_displays = set()
    if socket_path.is_dir():
        for file in socket_path.iterdir():
            if re.fullmatch(r"X[0-9]{1,3}", file.name):
                socket_displays.add(":" + file.name[1:])
    print(", ".join(sorted(socket_displays)) or "No X11 socket found")

    print("=== Ubuntu X11 authorization (never prints Xauthority cookie) ===")
    proc_displays, proc_auths = proc_display_auth(uid)
    auths = {str(HOME / ".Xauthority"), f"/run/user/{uid}/gdm/Xauthority"} | proc_auths
    runtime = Path(f"/run/user/{uid}")
    if runtime.is_dir():
        auths.update(str(path) for path in runtime.glob(".mutter-Xwaylandauth.*"))
    owned_auths = sorted(path for path in auths if permitted_auth(path, uid))
    print(f"Ubuntu-owned candidate auth files: {len(owned_auths)}")
    displays = sorted(socket_displays | proc_displays)
    print("Ubuntu process display assignments:",
          ", ".join(sorted(proc_displays)) if proc_displays else "none")
    working: list[tuple[str, str]] = []
    for display in displays[:LIMIT]:
        for authority in owned_auths[:LIMIT]:
            argv = ["runuser", "-u", USER, "--", "env",
                    f"DISPLAY={display}", f"XAUTHORITY={authority}",
                    "xdotool", "getdisplaygeometry"]
            code, geometry = cmd(argv, timeout=3)
            if code == 0:
                working.append((display, authority))
                print(f"AUTHORIZED user={USER} display={display} geometry={geometry} "
                      f"authority={authority}")
                break
    if working:
        print("RESULT: ubuntu can access at least one X display.")
        print("NEXT: verify it is the visible XRDP display and that no live Firefox profile is in use.")
        return 0
    print("RESULT: NO AUTHORIZED UBUNTU DISPLAY. Do not start Firefox or change X access rules.")
    print("If display is owned by desktop rather than ubuntu, use a proper ubuntu login session,")
    print("or migrate the worker under the intended display owner with explicit profile ownership.")
    return 3


if __name__ == "__main__":
    sys.exit(main())

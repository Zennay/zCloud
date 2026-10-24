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
import signal
import stat
import subprocess
import sys

EXPECTED_HOST = "VM-7eb5ee16-2c71-440d-b7fd-8095d204e930"
USER = "ubuntu"
HOME = Path("/home/ubuntu")
LIMIT = 8
MAX_PROC_ENTRIES = 20000
MAX_PROC_STAT_BYTES = 4096
MAX_PROC_ENV_BYTES = 65536
MAX_TOTAL_PROC_ENV_BYTES = 4 * 1024 * 1024
MAX_PROC_CANDIDATES = 64
MAX_SOCKET_CANDIDATES = 64


def cmd(argv: list[str], timeout: int = 4) -> tuple[int, str]:
    """Bound the whole descendant process group; don't block on inherited pipes."""
    try:
        p = subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, start_new_session=True,
        )
    except OSError:
        return (127, "")
    try:
        stdout, _ = p.communicate(timeout=timeout)
        return (p.returncode, stdout.strip())
    except subprocess.TimeoutExpired:
        # systemctl --user -M can leave children holding stdout after a timed-out
        # parent; killing only the parent can make subprocess.run() hang forever.
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            p.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            if p.stdout:
                p.stdout.close()
        return (124, "")


def _proc_start_ticks(process: Path, expected_pid: int) -> tuple[int | None, str | None]:
    """Read one bounded kernel process-lifetime identity without exporting it."""
    fd = None
    try:
        fd = os.open(
            str(process / "stat"),
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0),
        )
        raw = os.read(fd, MAX_PROC_STAT_BYTES + 1)
    except (FileNotFoundError, ProcessLookupError):
        return None, "vanished"
    except PermissionError:
        return None, "unreadable"
    except OSError:
        return None, "unavailable"
    finally:
        if fd is not None:
            os.close(fd)
    if not raw or len(raw) > MAX_PROC_STAT_BYTES:
        return None, "oversized_or_empty"
    try:
        value = raw.decode("ascii")
    except UnicodeError:
        return None, "malformed"
    left, right = value.find("("), value.rfind(")")
    if left < 0 or right <= left:
        return None, "malformed"
    try:
        observed_pid = int(value[:left].strip())
        fields = value[right + 2:].split()
        if observed_pid != expected_pid or len(fields) < 20:
            return None, "identity_mismatch"
        start_ticks = int(fields[19])
    except (ValueError, IndexError):
        return None, "malformed"
    if start_ticks < 0:
        return None, "malformed"
    return start_ticks, None


def _read_process_environ(process: Path, limit: int) -> tuple[bytes | None, str | None]:
    """Read a capped environment snapshot; never expose the raw bytes."""
    fd = None
    try:
        fd = os.open(
            str(process / "environ"),
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0),
        )
        raw = os.read(fd, limit + 1)
    except (FileNotFoundError, ProcessLookupError):
        return None, "vanished"
    except PermissionError:
        return None, "unreadable"
    except OSError:
        return None, "unavailable"
    finally:
        if fd is not None:
            os.close(fd)
    if len(raw) > limit:
        return None, "oversized"
    return raw, None


def proc_display_auth(uid: int) -> tuple[set[str], set[str], bool]:
    """Return bounded display/auth candidates and whether the scan was complete."""
    displays: set[str] = set()
    auths: set[str] = set()
    complete = True
    scanned = 0
    total_env_bytes = 0
    try:
        with os.scandir("/proc") as entries:
            for entry in entries:
                if not entry.name.isdecimal():
                    continue
                scanned += 1
                if scanned > MAX_PROC_ENTRIES:
                    complete = False
                    break
                pid = int(entry.name)
                process = Path("/proc") / entry.name
                start_before, start_error = _proc_start_ticks(process, pid)
                if start_error == "vanished":
                    continue
                try:
                    before = os.stat(process, follow_symlinks=False)
                except (FileNotFoundError, ProcessLookupError):
                    continue
                except OSError:
                    complete = False
                    continue
                if not stat.S_ISDIR(before.st_mode):
                    complete = False
                    continue
                if before.st_uid != uid:
                    continue
                if start_before is None:
                    complete = False
                    continue

                remaining = MAX_TOTAL_PROC_ENV_BYTES - total_env_bytes
                if remaining <= 0:
                    complete = False
                    break
                read_limit = min(MAX_PROC_ENV_BYTES, remaining)
                env, env_error = _read_process_environ(process, read_limit)
                if env_error == "vanished":
                    continue
                if env is None:
                    complete = False
                    # An oversized record consumed the current bounded read;
                    # stop instead of repeatedly reading beyond the global budget.
                    if env_error == "oversized":
                        break
                    continue
                total_env_bytes += len(env)

                start_after, after_error = _proc_start_ticks(process, pid)
                if after_error == "vanished":
                    continue
                try:
                    after = os.stat(process, follow_symlinks=False)
                except (FileNotFoundError, ProcessLookupError):
                    continue
                except OSError:
                    complete = False
                    continue
                if (
                    start_after is None or start_after != start_before
                    or not stat.S_ISDIR(after.st_mode)
                    or (before.st_dev, before.st_ino, before.st_uid)
                    != (after.st_dev, after.st_ino, after.st_uid)
                ):
                    complete = False
                    continue

                candidate_limit_hit = False
                for item in env.split(bytes((0,))):
                    if item.startswith(b"DISPLAY="):
                        value = item.partition(b"=")[2].decode("utf-8", "replace")
                        if re.fullmatch(r":[0-9]{1,3}(?:\.[0-9])?", value):
                            if value not in displays and len(displays) >= MAX_PROC_CANDIDATES:
                                complete = False
                                candidate_limit_hit = True
                                break
                            displays.add(value)
                    elif item.startswith(b"XAUTHORITY="):
                        value = item.partition(b"=")[2].decode("utf-8", "replace")
                        if (value.startswith("/") and len(value) <= 240
                                and not any(ord(char) < 32 or ord(char) == 127 for char in value)):
                            if value not in auths and len(auths) >= MAX_PROC_CANDIDATES:
                                complete = False
                                candidate_limit_hit = True
                                break
                            auths.add(value)
                if candidate_limit_hit:
                    break
    except OSError:
        complete = False
    return displays, auths, complete


def permitted_auth(path: str, uid: int) -> bool:
    """Accept only a stable, non-symlink, owner-private regular auth file."""
    candidate = Path(path)
    fd = None
    try:
        before = candidate.lstat()
        mode = stat.S_IMODE(before.st_mode)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != uid
                or mode & 0o077 or not mode & 0o400):
            return False
        fd = os.open(
            str(candidate),
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0),
        )
        opened = os.fstat(fd)
        return (
            stat.S_ISREG(opened.st_mode) and opened.st_uid == uid
            and not (stat.S_IMODE(opened.st_mode) & 0o077)
            and bool(stat.S_IMODE(opened.st_mode) & 0o400)
            and (before.st_dev, before.st_ino) == (opened.st_dev, opened.st_ino)
        )
    except OSError:
        return False
    finally:
        if fd is not None:
            os.close(fd)


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
    socket_displays: set[str] = set()
    socket_scan_complete = True
    try:
        socket_info = socket_path.lstat()
        if stat.S_ISDIR(socket_info.st_mode):
            with os.scandir(socket_path) as entries:
                for index, entry in enumerate(entries, start=1):
                    if index > MAX_SOCKET_CANDIDATES:
                        socket_scan_complete = False
                        break
                    if re.fullmatch(r"X[0-9]{1,3}", entry.name):
                        display = ":" + entry.name[1:]
                        if display not in socket_displays and len(socket_displays) >= MAX_PROC_CANDIDATES:
                            socket_scan_complete = False
                            break
                        socket_displays.add(display)
        else:
            socket_scan_complete = False
    except FileNotFoundError:
        pass
    except OSError:
        socket_scan_complete = False
    print(", ".join(sorted(socket_displays)) or "No X11 socket found")
    if not socket_scan_complete:
        print("X11 socket coverage: PARTIAL/UNKNOWN")

    print("=== Ubuntu X11 authorization (never prints Xauthority cookie) ===")
    proc_displays, proc_auths, proc_scan_complete = proc_display_auth(uid)
    print("Ubuntu process environment coverage:", "COMPLETE" if proc_scan_complete else "PARTIAL/UNKNOWN")
    if not proc_scan_complete:
        print("Coverage caveat: missing DISPLAY/XAUTHORITY values are unknown, not absent.")

    auth_candidates_complete = True
    auths = {str(HOME / ".Xauthority"), f"/run/user/{uid}/gdm/Xauthority"}
    for path in sorted(proc_auths):
        if path in auths:
            continue
        if len(auths) >= MAX_PROC_CANDIDATES:
            auth_candidates_complete = False
            break
        auths.add(path)
    runtime = Path(f"/run/user/{uid}")
    try:
        runtime_info = runtime.lstat()
        if stat.S_ISDIR(runtime_info.st_mode):
            with os.scandir(runtime) as entries:
                for entry in entries:
                    if not entry.name.startswith(".mutter-Xwaylandauth."):
                        continue
                    candidate = str(runtime / entry.name)
                    if candidate in auths:
                        continue
                    if len(auths) >= MAX_PROC_CANDIDATES:
                        auth_candidates_complete = False
                        break
                    auths.add(candidate)
        else:
            auth_candidates_complete = False
    except FileNotFoundError:
        pass
    except OSError:
        auth_candidates_complete = False
    owned_auths = sorted(path for path in auths if permitted_auth(path, uid))
    print(f"Ubuntu-owned candidate auth files: {len(owned_auths)}")
    displays = sorted(socket_displays | proc_displays)
    print("Ubuntu process display assignments:",
          ", ".join(sorted(proc_displays)) if proc_displays else "none")
    probe_coverage_complete = (
        proc_scan_complete and socket_scan_complete and auth_candidates_complete
        and len(displays) <= LIMIT and len(owned_auths) <= LIMIT
    )
    print("Display/auth probe coverage:", "COMPLETE" if probe_coverage_complete else "PARTIAL/UNKNOWN")
    if not probe_coverage_complete:
        print("Coverage caveat: capped candidate lists or unreadable sources mean absence is not proved.")
    working: list[tuple[str, str]] = []
    for display in displays[:LIMIT]:
        for authority in owned_auths[:LIMIT]:
            argv = ["runuser", "-u", USER, "--", "env",
                    f"DISPLAY={display}", f"XAUTHORITY={authority}",
                    "xdotool", "getdisplaygeometry"]
            if not permitted_auth(authority, uid):
                continue
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
    if probe_coverage_complete:
        print("RESULT: NO AUTHORIZED UBUNTU DISPLAY. Do not start Firefox or change X access rules.")
    else:
        print("RESULT: NO AUTHORIZED UBUNTU DISPLAY PROVED; candidate search coverage is partial.")
        print("Do not start Firefox or change X access rules from incomplete evidence.")
    print("If display is owned by desktop rather than ubuntu, use a proper ubuntu login session,")
    print("or migrate the worker under the intended display owner with explicit profile ownership.")
    return 3


if __name__ == "__main__":
    sys.exit(main())

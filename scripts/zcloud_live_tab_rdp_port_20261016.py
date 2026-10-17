#!/usr/bin/env python3
"""Deny-first Firefox RDP port evidence for the live-tab version *read-only* probe.

Input is local ps output, not an authorization signal. The optional
kernel-executable attestation reads /proc metadata but never opens a network
connection or modifies a process/browser; caller separately gates RDP reads.
"""
from __future__ import annotations

import os
import re
from typing import Callable

PROCESS = re.compile(r"^\s*([1-9][0-9]{0,9})\s+([0-9]{1,10})\s+(\S+)(?=\s|$)")
FLAG = re.compile(r"(?:^|\s)-start-debugger-server(?=\s|$|=)")
PORT = re.compile(r"(?:^|\s)-start-debugger-server\s+([0-9]{1,5})(?=\s|$)")
CONTENTPROC = re.compile(r"(?:^|\s)-contentproc(?=\s|$)")
BINARIES = frozenset({"firefox", "firefox-bin"})
MAX_ROWS = 32768
MAX_CANDIDATE_LENGTH = 4096
MAX_PID = 4294967295

# Kernel-reported actual binary path, NOT spoofable argv[0]. Restricted to
# system-managed Firefox installations; unknown copies fail closed.
SYSTEM_FIREFOX = re.compile(
    r"^(?:/snap/firefox/[0-9]+/usr/lib/firefox/firefox(?:-bin)?"
    r"|/usr/lib/firefox/firefox(?:-bin)?"
    r"|/usr/lib/firefox-esr/firefox-esr"
    r"|/usr/bin/firefox(?:-bin)?)$"
)


def trusted_firefox_executable(pid: int) -> bool:
    """Read only /proc/PID/exe, never trust a user-supplied process title."""
    if not isinstance(pid, int) or isinstance(pid, bool) or not 1 <= pid <= MAX_PID:
        return False
    try:
        target = os.readlink(f"/proc/{pid}/exe")
    except (OSError, ValueError):
        return False
    return bool(SYSTEM_FIREFOX.fullmatch(target))



def detect_debugger_port(
    lines: object, *, verify_executable: Callable[[int], bool] | None = None
) -> tuple[str, int | None]:
    """Return (unique, port), (absent, None) or (ambiguous, None).

    A flag mentioned by a shell, Python interpreter or another non-Firefox
    command is *not* debugger evidence. It invalidates any apparent proof.
    """
    if not isinstance(lines, (tuple, list)) or len(lines) > MAX_ROWS:
        return "ambiguous", None
    found: list[int] = []
    seen: set[int] = set()
    for line in lines:
        if not isinstance(line, str):
            return "ambiguous", None
        if "-start-debugger-server" not in line or "firefox" not in line.lower():
            continue
        if (not line or len(line) > MAX_CANDIDATE_LENGTH or "\0" in line
                or "\r" in line or "\n" in line):
            return "ambiguous", None
        process = PROCESS.match(line)
        if not process:
            return "ambiguous", None
        pid, ppid = int(process.group(1)), int(process.group(2))
        exe = process.group(3).rsplit("/", 1)[-1].lower()
        if pid > MAX_PID or ppid > MAX_PID or exe not in BINARIES:
            return "ambiguous", None
        args = line[process.end():]
        if CONTENTPROC.search(args):
            continue
        if pid in seen or len(FLAG.findall(args)) != 1:
            return "ambiguous", None
        # ps argv[0] can be forged (exec -a firefox). The real manual workflow
        # supplies a kernel /proc exe check before accepting a local RDP port.
        # Never return a connectable port from process argv alone.
        # Callers must explicitly provide trustworthy executable evidence.
        if verify_executable is None:
            return "ambiguous", None
        try:
            if not verify_executable(pid):
                return "ambiguous", None
        except Exception:
            return "ambiguous", None
        seen.add(pid)
        ports = PORT.findall(args)
        if len(ports) != 1:
            return "ambiguous", None
        port = int(ports[0])
        if not 1 <= port <= 65535:
            return "ambiguous", None
        found.append(port)
    if not found:
        return "absent", None
    if len(found) != 1:
        return "ambiguous", None
    return "unique", found[0]

#!/usr/bin/env python3
"""Bounded, read-only Firefox debugger port identity extraction.

Only accept process rows from ps -eo pid=,ppid=,args= where the
executable itself is Firefox, never shell/grep/Python commands containing
Firefox text. No network, browser, process or file mutations are performed.
"""
from __future__ import annotations

import re

PROCESS = re.compile(
    r"^\s*([1-9][0-9]{0,9})\s+([0-9]{1,10})\s+(\S+)(?=\s|$)"
)
PORT = re.compile(r"(?:^|\s)-start-debugger-server\s+([0-9]{1,5})(?=\s|$)")
FLAG = re.compile(r"(?:^|\s)-start-debugger-server(?=\s|$)")
FIREFOX_BINARIES = frozenset({"firefox", "firefox-bin"})
MAX_PROCESSES = 16
MAX_COMMAND_LENGTH = 4096


def extract_ports(process_lines: object) -> list[int]:
    """Return ports for distinct Firefox processes; [] on any uncertainty."""
    if not isinstance(process_lines, (list, tuple)) or not 1 <= len(process_lines) <= MAX_PROCESSES:
        return []
    ports: list[int] = []
    pids: set[int] = set()
    for line in process_lines:
        if (not isinstance(line, str) or not line or len(line) > MAX_COMMAND_LENGTH
                or "\x00" in line or "\n" in line or "\r" in line):
            return []
        proc = PROCESS.match(line)
        if not proc:
            return []
        pid = int(proc.group(1))
        ppid = int(proc.group(2))
        exe = proc.group(3).rsplit("/", 1)[-1].lower()
        if (pid > 4294967295 or ppid > 4294967295 or pid in pids
                or exe not in FIREFOX_BINARIES):
            return []
        pids.add(pid)
        args = line[proc.end():]
        # A second malformed debugger flag cannot be ignored just because
        # it fails to match a well-formed numeric port.
        if len(FLAG.findall(args)) != 1:
            return []
        matches = list(PORT.finditer(args))
        if len(matches) != 1:
            return []
        port = int(matches[0].group(1))
        if not 1 <= port <= 65535:
            return []
        ports.append(port)
    # Two parents on one port remain ambiguous. Distinct ports are still
    # refused by the caller's len(ports) != 1 runtime gate.
    return ports if len(set(ports)) == len(ports) else []

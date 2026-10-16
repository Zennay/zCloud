#!/usr/bin/env python3
"""Bounded, read-only Firefox debugger port identity extraction.

This module never opens a socket, alters a tab, or writes VPS state.
"""
from __future__ import annotations

import re

PORT = re.compile(r"(?:^|\s)-start-debugger-server\s+([0-9]{1,5})(?=\s|$)")
MAX_PROCESSES = 16
MAX_COMMAND_LENGTH = 4096


def extract_ports(process_lines: object) -> list[int]:
    """Extract ports, returning [] on invalid or ambiguous process evidence."""
    if not isinstance(process_lines, (list, tuple)) or not 1 <= len(process_lines) <= MAX_PROCESSES:
        return []
    ports = []
    for line in process_lines:
        if (not isinstance(line, str) or not line or len(line) > MAX_COMMAND_LENGTH
                or "\x00" in line or "\n" in line or "\r" in line):
            return []
        if "firefox" not in line.lower() or "-start-debugger-server" not in line:
            return []
        matches = list(PORT.finditer(line))
        if len(matches) != 1:
            return []
        port = int(matches[0].group(1))
        if not 1 <= port <= 65535:
            return []
        ports.append(port)
    if len(set(ports)) != len(ports):
        return []
    return ports

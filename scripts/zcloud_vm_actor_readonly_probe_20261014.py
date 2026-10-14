#!/usr/bin/env python3
"""Explicitly authorized, metadata-only Firefox addon inventory.

Never introspect browser tabs, attach a watcher, read console messages, print
addon IDs or execute worker actions. The permanent zCloud runner and current
main SHA are separately admitted by the GitHub workflow before this executes.
"""
from __future__ import annotations

from contextlib import closing
import json
import re
import socket
import subprocess
from typing import Any

MAX_HEADER_DIGITS = 7
MAX_FRAME_BYTES = 131072
MAX_ADDONS = 100
PORT_PATTERN = re.compile(r"-start-debugger-server\s+(\d+)(?:\s|$)")


def unique_debugger_port(process_output: str) -> int:
    ports = [int(port) for port in PORT_PATTERN.findall(process_output)]
    if len(ports) != 1 or not 1 <= ports[0] <= 65535:
        raise ValueError("debugger_port_not_unique")
    return ports[0]


def read_frame(sock: Any) -> dict[str, Any]:
    digits = bytearray()
    while True:
        character = sock.recv(1)
        if not character:
            raise ValueError("frame_header_truncated")
        if character == b":":
            break
        if character < b"0" or character > b"9" or len(digits) >= MAX_HEADER_DIGITS:
            raise ValueError("bad_frame_header")
        digits.extend(character)
    if not digits:
        raise ValueError("empty_frame_header")
    length = int(digits.decode("ascii"))
    if length == 0 or length > MAX_FRAME_BYTES:
        raise ValueError("frame_size_denied")
    payload = bytearray()
    while len(payload) < length:
        chunk = sock.recv(min(4096, length - len(payload)))
        if not chunk:
            raise ValueError("frame_body_truncated")
        payload.extend(chunk)
    decoded = json.loads(payload)
    if not isinstance(decoded, dict):
        raise ValueError("untrusted_frame_shape")
    return decoded


def send_root_addon_inventory(sock: Any) -> None:
    # The only permitted RDP request. No getWatcher/watchTargets/console
    # or navigation commands, including during retries or error handling.
    body = b'{"to":"root","type":"listAddons"}'
    sock.sendall(str(len(body)).encode("ascii") + b":" + body)


def summarize_reply(reply: dict[str, Any]) -> dict[str, int | bool]:
    if reply.get("error"):
        raise ValueError("debugger_addon_error")
    addons = reply.get("addons")
    if not isinstance(addons, list) or len(addons) > MAX_ADDONS:
        raise ValueError("invalid_addon_inventory")
    matches = []
    for addon in addons:
        if not isinstance(addon, dict):
            raise ValueError("invalid_addon_entry")
        identifier = addon.get("id")
        if not isinstance(identifier, str):
            raise ValueError("invalid_addon_id")
        if "aecec67f" in identifier or "project-runner" in identifier:
            matches.append(addon)
    return {
        "addon_inventory_observed": True,
        "relevant_addon_count": len(matches),
        "debuggable_count": sum(addon.get("debuggable") is True for addon in matches),
        "live_worker_generation_verified": False,
        "production_ready_verified": False,
    }


def inspect(process_output: str, connection_factory=socket.create_connection) -> dict[str, int | bool]:
    port = unique_debugger_port(process_output)
    with closing(connection_factory(("127.0.0.1", port), timeout=4)) as sock:
        sock.settimeout(6)
        greeting = read_frame(sock)
        if greeting.get("from") != "root":
            raise ValueError("untrusted_debugger_greeting")
        send_root_addon_inventory(sock)
        return summarize_reply(read_frame(sock))


def main() -> int:
    try:
        process = subprocess.run(
            ["ps", "-eo", "args="], text=True, capture_output=True,
            check=True, timeout=5,
        )
        result = inspect(process.stdout)
    except (OSError, ValueError, TypeError, socket.timeout, subprocess.SubprocessError):
        # Never echo raw Firefox/RDP errors, profile paths, session IDs or URLs.
        print("VM_METADATA_PROBE_UNAVAILABLE=1")
        return 2
    print("VM_METADATA_PROBE=" + json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

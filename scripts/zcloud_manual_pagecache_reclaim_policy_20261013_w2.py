#!/usr/bin/env python3
"""Read-only, deny-first clean page-cache reclaim eligibility.

A positive decision is advisory only. It cannot authorize deployment or root
actions; a separate manual exact-main operator approval is required.
"""
import json
from pathlib import Path
import re
import sys

MEMINFO = Path("/proc/meminfo")
MAX_BYTES = 16384
KIB = 1024
LOW = 512 * KIB
HIGH = 2048 * KIB
MIN_CACHE = 256 * KIB
MAX_DIRTY = 128 * KIB
REQUIRED = ("MemTotal", "MemAvailable", "Cached", "Dirty", "Writeback")
FIELD = re.compile(r"^([A-Za-z][A-Za-z0-9_]*):\s+([0-9]+)\s+kB$")


class Denied(ValueError):
    pass


def parse_meminfo(raw):
    if not isinstance(raw, str) or not raw or len(raw.encode("utf-8")) > MAX_BYTES:
        raise Denied("missing or oversized memory evidence")
    values = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        match = FIELD.fullmatch(line)
        if not match:
            raise Denied("malformed memory evidence")
        key = match.group(1)
        if key in values:
            raise Denied("duplicate memory field")
        values[key] = int(match.group(2))
    if any(name not in values for name in REQUIRED):
        raise Denied("missing required memory fields")
    total = values["MemTotal"]
    if total < LOW or any(values[key] > total for key in REQUIRED if key != "MemTotal"):
        raise Denied("inconsistent memory evidence")
    return values


def decision(values):
    if not isinstance(values, dict) or any(type(values.get(k)) is not int for k in REQUIRED):
        raise Denied("missing numeric evidence")
    available = values["MemAvailable"]
    if available < LOW:
        return False, "critical-memory-protect"
    if available >= HIGH:
        return False, "no-memory-pressure"
    if values["Cached"] < MIN_CACHE:
        return False, "insufficient-clean-cache"
    if values["Dirty"] >= MAX_DIRTY or values["Writeback"]:
        return False, "writeback-busy"
    return True, "bounded-clean-cache-reclaim-eligible"


def main(source=MEMINFO):
    try:
        if source.is_symlink() or not source.is_file():
            raise Denied("memory evidence path not regular")
        with source.open("r", encoding="utf-8") as fp:
            raw = fp.read(MAX_BYTES + 1)
        info = parse_meminfo(raw)
        accepted, reason = decision(info)
    except (Denied, OSError, UnicodeError, ValueError, TypeError) as exc:
        print(json.dumps({
            "eligible": False, "reason": type(exc).__name__,
            "mutation_performed": False, "release_authorized": False,
        }, sort_keys=True))
        return 2
    print(json.dumps({
        "eligible": accepted, "reason": reason,
        "mutation_performed": False, "release_authorized": False,
    }, sort_keys=True))
    return 0 if accepted else 3


if __name__ == "__main__":
    sys.exit(main())

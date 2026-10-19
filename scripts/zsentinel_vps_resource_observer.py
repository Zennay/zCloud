#!/usr/bin/env python3
"""One-shot Linux VPS capacity observation for the read-only zSentinel cockpit.

This is advisory evidence only. No service, queue, runner, claim or process
is modified. Invoke only on the intended VPS, with an existing database path.
"""
from __future__ import annotations

import argparse
import math
import os
import stat
from datetime import datetime, timezone
from pathlib import Path


def _memory_values():
    """Only kernel-provided scalar MiB data; never expose raw /proc content."""
    seen = {}
    with open("/proc/meminfo", "r", encoding="ascii") as source:
        for line in source:
            fields = line.split()
            if len(fields) == 3 and fields[0] in ("MemAvailable:", "SwapFree:"):
                if fields[2] != "kB" or not fields[1].isascii() or not fields[1].isdigit():
                    raise ValueError("invalid Linux memory counter")
                seen[fields[0]] = int(fields[1]) / 1024
    if set(seen) != {"MemAvailable:", "SwapFree:"}:
        raise ValueError("incomplete kernel memory counters")
    return seen["MemAvailable:"], seen["SwapFree:"]


def observe_vps_capacity(database: Path):
    """Read-only sample of the host running this command, never a remote VPS."""
    if os.name != "posix" or not Path("/proc/meminfo").is_file():
        raise ValueError("Linux /proc resource counters required")
    if not isinstance(database, Path):
        raise ValueError("database must be a Path")
    # Anchor the disk measurement to the actual existing database mount,
    # rather than an unrelated filesystem with more free space.
    if database.is_symlink() or not database.is_file():
        raise ValueError("existing non-symlink database required for disk evidence")
    db_stat = database.stat()
    if not stat.S_ISREG(db_stat.st_mode):
        raise ValueError("database must be a regular file")
    cores = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else os.cpu_count()
    if type(cores) is not int or not 1 <= cores <= 128:
        raise ValueError("invalid CPU affinity")
    load = os.getloadavg()[1]
    available, swap = _memory_values()
    fs = os.statvfs(database)
    free = fs.f_bavail * fs.f_frsize / (1024 ** 3)
    vals = (load, available, swap, free)
    if any(not math.isfinite(value) or value < 0 for value in vals):
        raise ValueError("invalid host resource observation")
    return {
        "source": "trusted_readonly_vps_snapshot",
        "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "vps": {
            "cores": cores,
            "load_5m": round(load, 3),
            "available_memory_mib": round(available, 1),
            "available_swap_mib": round(swap, 1),
            "free_disk_gib": round(free, 3),
        },
    }


def main():
    parser = argparse.ArgumentParser(description="Read one local Linux VPS capacity snapshot; no writes")
    parser.add_argument("--database", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(__import__("json").dumps(observe_vps_capacity(args.database), sort_keys=True))
    except (OSError, ValueError) as error:
        parser.exit(2, f"zSentinel VPS resource observation refused: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

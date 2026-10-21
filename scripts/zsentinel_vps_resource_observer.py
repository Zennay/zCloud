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
    # Pin the filesystem to one existing regular database inode. A path
    # may change between is_file/stat/statvfs on a busy host; fd evidence
    # prevents an unrelated mount from being mistaken for queue headroom.
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "fstatvfs"):
        raise ValueError("Linux nofollow and fstatvfs required")
    flags = (os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0)
             | getattr(os, "O_CLOEXEC", 0))
    try:
        fd = os.open(database, flags)
    except OSError as exc:
        raise ValueError("existing non-symlink database required") from exc
    try:
        db_stat = os.fstat(fd)
        if not stat.S_ISREG(db_stat.st_mode):
            raise ValueError("database must be a regular file")
        # A rename/symlink swap while opening is not a trusted observation.
        current_path = os.stat(database, follow_symlinks=False)
        if (current_path.st_dev, current_path.st_ino) != (db_stat.st_dev, db_stat.st_ino):
            raise ValueError("database path changed during resource observation")
        fs = os.fstatvfs(fd)
    finally:
        os.close(fd)
    cores = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else os.cpu_count()
    if type(cores) is not int or not 1 <= cores <= 128:
        raise ValueError("invalid CPU affinity")
    load = os.getloadavg()[1]
    available, swap = _memory_values()
    if fs.f_blocks <= 0 or fs.f_frsize <= 0 or not 0 <= fs.f_bavail <= fs.f_blocks:
        raise ValueError("invalid database-mount capacity")
    free = fs.f_bavail * fs.f_frsize / (1024 ** 3)
    # Account for blocks unavailable to the unprivileged runtime (including
    # filesystem reserves); 94% occupied with 6 GiB free still matters.
    unavailable_percent = 100 * (1 - fs.f_bavail / fs.f_blocks)
    # Byte headroom is not inode headroom; creating jobs/logs can fail despite free bytes.
    if any(type(x) is not int or x < 0 for x in (fs.f_files, fs.f_ffree, fs.f_favail)):
        raise ValueError("invalid database-mount inode accounting")
    if fs.f_files > 0:
        if fs.f_ffree > fs.f_files or fs.f_favail > fs.f_ffree:
            raise ValueError("contradictory database-mount inode accounting")
        inode_available = fs.f_favail
        inode_total = fs.f_files
        inode_unavailable_percent = round(100 * (1 - inode_available / inode_total), 2)
        inode_source = "same_database_mount_statvfs"
    else:
        inode_available = inode_total = inode_unavailable_percent = None
        inode_source = "unsupported_or_unknown_not_zero"
    vals = (load, available, swap, free, unavailable_percent)
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
            "disk_unavailable_percent": round(unavailable_percent, 2),
            "inode_available": inode_available,
            "inode_total": inode_total,
            "inode_unavailable_percent": inode_unavailable_percent,
            "inode_source": inode_source,
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

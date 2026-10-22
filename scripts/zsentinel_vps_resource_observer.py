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



def _archive_mount_capacity(archive_target: Path | None, root_device: int,
                            database_device: int) -> dict[str, object]:
    """Report aggregate archive-mount capacity only from a pinned directory fd."""
    result: dict[str, object] = {
        "configured": archive_target is not None,
        "state": "not_requested" if archive_target is None else "unavailable",
        "mount_point_detected": None,
        "same_device_as_root": None,
        "same_device_as_database": None,
        "free_disk_gib": None,
        "disk_unavailable_percent": None,
        "inode_available": None,
        "inode_total": None,
        "inode_unavailable_percent": None,
        "source": "same_archive_mount_fstatvfs" if archive_target is not None else "not_requested",
        "source_files_inspected": False,
        "offload_candidate_verified": False,
        "offload_authorized": False,
        "mutation_performed": False,
    }
    if archive_target is None:
        return result
    if not isinstance(archive_target, Path) or not archive_target.is_absolute():
        raise ValueError("archive capacity target must be an explicit absolute path")

    fd = None
    try:
        flags = (os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW |
                 getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0))
        fd = os.open(archive_target, flags)
        opened = os.fstat(fd)
        path_info = os.stat(archive_target, follow_symlinks=False)
        if (not stat.S_ISDIR(opened.st_mode)
                or (opened.st_dev, opened.st_ino) != (path_info.st_dev, path_info.st_ino)):
            raise ValueError("archive target identity changed")
        fs = os.fstatvfs(fd)
        if (fs.f_blocks <= 0 or fs.f_frsize <= 0
                or not 0 <= fs.f_bavail <= fs.f_blocks):
            raise ValueError("invalid archive target capacity")
        if any(type(x) is not int or x < 0 for x in
               (fs.f_files, fs.f_ffree, fs.f_favail)):
            raise ValueError("invalid archive target inode counters")
        if fs.f_files > 0 and (
                fs.f_ffree > fs.f_files or fs.f_favail > fs.f_ffree):
            raise ValueError("contradictory archive target inode counters")

        mount_detected = os.path.ismount(archive_target)
        same_root = opened.st_dev == root_device
        same_database = opened.st_dev == database_device
        free_gib = fs.f_bavail * fs.f_frsize / (1024 ** 3)
        unavailable_percent = 100 * (1 - fs.f_bavail / fs.f_blocks)
        if (not math.isfinite(free_gib) or free_gib < 0 or free_gib > 100000
                or not math.isfinite(unavailable_percent)
                or not 0 <= unavailable_percent <= 100):
            raise ValueError("invalid archive target observation")
        if fs.f_files > 0:
            inode_available = fs.f_favail
            inode_total = fs.f_files
            inode_unavailable = round(100 * (1 - inode_available / inode_total), 2)
        else:
            inode_available = inode_total = inode_unavailable = None

        if mount_detected and not same_root:
            state = "observed_external_mount"
        elif same_root:
            state = "same_as_root_filesystem"
        else:
            state = "mount_point_not_verified"
        result.update({
            "state": state,
            "mount_point_detected": mount_detected,
            "same_device_as_root": same_root,
            "same_device_as_database": same_database,
            "free_disk_gib": round(free_gib, 3),
            "disk_unavailable_percent": round(unavailable_percent, 2),
            "inode_available": inode_available,
            "inode_total": inode_total,
            "inode_unavailable_percent": inode_unavailable,
        })
    except (OSError, ValueError, TypeError, OverflowError, AttributeError, ZeroDivisionError):
        # An optional archive probe must not erase a valid root/database sample.
        result["state"] = "unavailable"
    finally:
        if fd is not None:
            os.close(fd)
    return result



def observe_vps_capacity(database: Path, *, archive_target: Path | None = None):
    """Read-only sample of host and optional archive capacity; never a remote VPS."""
    if archive_target is not None and (
            not isinstance(archive_target, Path) or not archive_target.is_absolute()):
        raise ValueError("archive capacity target must be an explicit absolute path")
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
    # The queue database can live on a large mounted archive while the
    # host root filesystem containing runner executables/logs is nearly full.
    # Observe root separately: healthy database storage is NOT proof that
    # new runner jobs can start safely on the system mount.
    root_flags = (os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW |
                  getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0))
    root_fd = os.open("/", root_flags)
    try:
        root_identity = os.fstat(root_fd)
        root_fs = os.fstatvfs(root_fd)
    finally:
        os.close(root_fd)
    if (not stat.S_ISDIR(root_identity.st_mode)
            or root_fs.f_blocks <= 0 or root_fs.f_frsize <= 0
            or not 0 <= root_fs.f_bavail <= root_fs.f_blocks):
        raise ValueError("invalid VPS root filesystem capacity")
    root_free = root_fs.f_bavail * root_fs.f_frsize / (1024 ** 3)
    root_unavailable_pct = 100 * (1 - root_fs.f_bavail / root_fs.f_blocks)
    if any(type(x) is not int or x < 0 for x in
           (root_fs.f_files, root_fs.f_ffree, root_fs.f_favail)):
        raise ValueError("invalid VPS root inode accounting")
    if root_fs.f_files > 0:
        if root_fs.f_ffree > root_fs.f_files or root_fs.f_favail > root_fs.f_ffree:
            raise ValueError("contradictory VPS root inode counters")
        root_inode_available = root_fs.f_favail
        root_inode_total = root_fs.f_files
        root_inode_pct = round(
            100 * (1 - root_inode_available / root_inode_total), 2)
        root_inode_source = "root_directory_fstatvfs"
    else:
        root_inode_available = root_inode_total = root_inode_pct = None
        root_inode_source = "unsupported_or_unknown_not_zero"
    if (not math.isfinite(root_free) or root_free < 0
            or root_free > 100000
            or not math.isfinite(root_unavailable_pct)
            or not 0 <= root_unavailable_pct <= 100):
        raise ValueError("invalid VPS root mount observation")
    archive_capacity = _archive_mount_capacity(
        archive_target, root_identity.st_dev, db_stat.st_dev)
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
            # Existing unprefixed capacity remains the database mount.
            "free_disk_gib": round(free, 3),
            "disk_unavailable_percent": round(unavailable_percent, 2),
            "root_free_disk_gib": round(root_free, 3),
            "root_disk_unavailable_percent": round(root_unavailable_pct, 2),
            "root_inode_available": root_inode_available,
            "root_inode_total": root_inode_total,
            "root_inode_unavailable_percent": root_inode_pct,
            "root_inode_source": root_inode_source,
            "root_mount_same_device_as_database": (
                root_identity.st_dev == db_stat.st_dev),
            "archive_target": archive_capacity,
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

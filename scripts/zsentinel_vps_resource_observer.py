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


MAX_PROC_MEMINFO_BYTES = 16384


def _memory_values():
    """Read only bounded scalar memory/swap counters; never return raw proc data."""
    fd = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
        fd = os.open("/proc/meminfo", flags)
        raw = os.read(fd, MAX_PROC_MEMINFO_BYTES + 1)
    except OSError as exc:
        raise ValueError("Linux memory counters unavailable") from exc
    finally:
        if fd is not None:
            os.close(fd)
    if not raw or len(raw) > MAX_PROC_MEMINFO_BYTES:
        raise ValueError("Linux memory counter source exceeds its byte cap")
    try:
        lines = raw.decode("ascii").splitlines()
    except UnicodeError as exc:
        raise ValueError("invalid Linux memory counter encoding") from exc

    required = {"MemTotal:", "MemAvailable:", "SwapTotal:", "SwapFree:"}
    seen = {}
    for line in lines:
        fields = line.split()
        if not fields or fields[0] not in required:
            continue
        if (fields[0] in seen or len(fields) != 3 or fields[2] != "kB"
                or not fields[1].isascii() or not fields[1].isdigit()):
            raise ValueError("invalid Linux memory counter")
        seen[fields[0]] = int(fields[1]) / 1024
    if set(seen) != required:
        raise ValueError("incomplete kernel memory counters")
    if (seen["MemTotal:"] <= 0 or seen["MemAvailable:"] > seen["MemTotal:"]
            or seen["SwapFree:"] > seen["SwapTotal:"]):
        raise ValueError("inconsistent kernel memory counters")
    return (seen["MemAvailable:"], seen["MemTotal:"],
            seen["SwapFree:"], seen["SwapTotal:"])



def _floor_precision(value: float, places: int) -> float:
    """Round nonnegative capacity/headroom down so thresholds cannot be softened."""
    scale = 10 ** places
    return math.floor(value * scale) / scale


def _ceil_precision(value: float, places: int) -> float:
    """Round nonnegative pressure percentages/load up so thresholds fail closed."""
    scale = 10 ** places
    return math.ceil(value * scale) / scale


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
        path_after_mount_probe = os.stat(archive_target, follow_symlinks=False)
        if ((path_after_mount_probe.st_dev, path_after_mount_probe.st_ino)
                != (opened.st_dev, opened.st_ino)):
            raise ValueError("archive target changed during mount-point observation")
        same_root = opened.st_dev == root_device
        same_database = opened.st_dev == database_device
        free_gib = fs.f_bavail * fs.f_frsize / (1024 ** 3)
        unavailable_percent = _ceil_precision(100 * (1 - fs.f_bavail / fs.f_blocks), 2)
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
    load = _ceil_precision(os.getloadavg()[1], 3)
    available, total_memory, swap, total_swap = _memory_values()
    available = _floor_precision(available, 1)
    swap = _floor_precision(swap, 1)
    available_memory_percent = _floor_precision(100 * available / total_memory, 2)
    swap_used_percent = (_ceil_precision(100 * (total_swap - swap) / total_swap, 2)
                         if total_swap > 0 else None)
    load_5m_per_core = _ceil_precision(load / cores, 3)
    if fs.f_blocks <= 0 or fs.f_frsize <= 0 or not 0 <= fs.f_bavail <= fs.f_blocks:
        raise ValueError("invalid database-mount capacity")
    free = _floor_precision(fs.f_bavail * fs.f_frsize / (1024 ** 3), 3)
    # Account for blocks unavailable to the unprivileged runtime (including
    # filesystem reserves); 94% occupied with 6 GiB free still matters.
    unavailable_percent = _ceil_precision(100 * (1 - fs.f_bavail / fs.f_blocks), 2)
    # Byte headroom is not inode headroom; creating jobs/logs can fail despite free bytes.
    if any(type(x) is not int or x < 0 for x in (fs.f_files, fs.f_ffree, fs.f_favail)):
        raise ValueError("invalid database-mount inode accounting")
    if fs.f_files > 0:
        if fs.f_ffree > fs.f_files or fs.f_favail > fs.f_ffree:
            raise ValueError("contradictory database-mount inode accounting")
        inode_available = fs.f_favail
        inode_total = fs.f_files
        inode_unavailable_percent = _ceil_precision(
            100 * (1 - inode_available / inode_total), 2)
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
    root_free = _floor_precision(root_fs.f_bavail * root_fs.f_frsize / (1024 ** 3), 3)
    root_unavailable_pct = _ceil_precision(
        100 * (1 - root_fs.f_bavail / root_fs.f_blocks), 2)
    if any(type(x) is not int or x < 0 for x in
           (root_fs.f_files, root_fs.f_ffree, root_fs.f_favail)):
        raise ValueError("invalid VPS root inode accounting")
    if root_fs.f_files > 0:
        if root_fs.f_ffree > root_fs.f_files or root_fs.f_favail > root_fs.f_ffree:
            raise ValueError("contradictory VPS root inode counters")
        root_inode_available = root_fs.f_favail
        root_inode_total = root_fs.f_files
        root_inode_pct = _ceil_precision(
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
    def disk_band(unavailable_pct, free_gib, inode_pct):
        if (unavailable_pct >= 95 or free_gib < 1
                or (inode_pct is not None and inode_pct >= 95)):
            return "critical"
        if (unavailable_pct >= 90 or free_gib < 5
                or (inode_pct is not None and inode_pct >= 90)):
            return "high"
        return "normal"

    root_band = disk_band(root_unavailable_pct, root_free, root_inode_pct)
    database_band = disk_band(unavailable_percent, free, inode_unavailable_percent)
    capacity_warnings = []
    if root_band == "critical":
        capacity_warnings.append("root_filesystem_critical")
    elif root_band == "high":
        capacity_warnings.append("root_filesystem_high")
    if database_band == "critical":
        capacity_warnings.append("database_mount_critical")
    elif database_band == "high":
        capacity_warnings.append("database_mount_high")
    if swap_used_percent is not None and swap_used_percent >= 90:
        capacity_warnings.append("swap_pressure_high")
    if available_memory_percent <= 10:
        capacity_warnings.append("memory_pressure_high")
    if load_5m_per_core >= 2:
        capacity_warnings.append("load_pressure_high")
    archive_has_capacity = (
        archive_capacity["state"] == "observed_external_mount"
        and type(archive_capacity.get("free_disk_gib")) in (int, float)
        and archive_capacity["free_disk_gib"] > 0
    )
    if root_band == "critical" and not archive_has_capacity:
        capacity_warnings.append("no_verified_external_archive_headroom")
    capacity_severity = (
        "critical" if root_band == "critical" or database_band == "critical"
        or (swap_used_percent is not None and swap_used_percent >= 98)
        else "high" if capacity_warnings else "normal"
    )
    vals = (load, available, total_memory, swap, total_swap, free,
            unavailable_percent, available_memory_percent, load_5m_per_core)
    if any(not math.isfinite(value) or value < 0 for value in vals):
        raise ValueError("invalid host resource observation")
    return {
        "source": "trusted_readonly_vps_snapshot",
        "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "vps": {
            "cores": cores,
            "load_5m": round(load, 3),
            "load_5m_per_core": round(load_5m_per_core, 3),
            "available_memory_mib": round(available, 1),
            "total_memory_mib": round(total_memory, 1),
            "available_memory_percent": round(available_memory_percent, 2),
            "available_swap_mib": round(swap, 1),
            "total_swap_mib": _ceil_precision(total_swap, 1),
            "swap_status": "OBSERVED" if total_swap > 0 else "NO_SWAP_CONFIGURED",
            "swap_used_percent": round(swap_used_percent, 2) if swap_used_percent is not None else None,
            "root_capacity_band": root_band,
            "database_capacity_band": database_band,
            "capacity_severity": capacity_severity,
            "capacity_warnings": capacity_warnings,
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

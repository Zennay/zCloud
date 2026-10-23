"""Read-only local root-filesystem capacity metadata for the zSentinel cockpit.

No cleanup, file reads, directory traversal, quota changes or deployment.
The returned unavailable percentage includes reserved filesystem blocks;
it must not be described as bytes consumed by project files.
"""
from __future__ import annotations

import os


def observe_root_disk():
    packet = {
        "source": "local_root_statvfs",
        "status": "UNKNOWN",
        "available_gib": None,
        "unavailable_percent": None,
        "space_pressure_hint": "UNKNOWN",
        "inode_status": "UNKNOWN",
        "available_inodes": None,
        "unavailable_inode_percent": None,
        "inode_pressure_hint": "UNKNOWN",
        "pressure_hint": "UNKNOWN",
        "cleanup_authorized": False,
        "new_worker_authorized": False,
    }
    try:
        fs = os.statvfs("/")
        if not (0 < fs.f_frsize <= 2**30
                and 0 < fs.f_blocks <= 2**54
                and 0 <= fs.f_bavail <= fs.f_blocks):
            return packet
        available = fs.f_bavail * fs.f_frsize
        unavailable = 100.0 * (fs.f_blocks - fs.f_bavail) / fs.f_blocks
        space_pressure = (
            "HIGH" if available < 2 * 1024**3 or unavailable >= 95
            else "ELEVATED" if available < 5 * 1024**3 or unavailable >= 90
            else "NO_LOCAL_THRESHOLD_CROSSED"
        )
        packet.update({
            "status": "ROOT_STATVFS_OBSERVED",
            "available_gib": round(available / 1024**3, 2),
            "unavailable_percent": round(unavailable, 1),
            "space_pressure_hint": space_pressure,
        })

        # Inode exhaustion can prevent creates/logs even when free bytes look
        # healthy. Keep it as an independent signal: unsupported/zero totals
        # are UNKNOWN, never interpreted as unlimited capacity.
        if (0 < fs.f_files <= 2**54
                and 0 <= fs.f_favail <= fs.f_files):
            inode_unavailable = 100.0 * (fs.f_files - fs.f_favail) / fs.f_files
            inode_pressure = (
                "HIGH" if inode_unavailable >= 95
                else "ELEVATED" if inode_unavailable >= 90
                else "NO_LOCAL_THRESHOLD_CROSSED"
            )
            packet.update({
                "inode_status": "OBSERVED",
                "available_inodes": int(fs.f_favail),
                "unavailable_inode_percent": round(inode_unavailable, 1),
                "inode_pressure_hint": inode_pressure,
            })
        packet["pressure_hint"] = (
            "HIGH" if "HIGH" in (
                packet["space_pressure_hint"], packet["inode_pressure_hint"]
            ) else
            "ELEVATED" if "ELEVATED" in (
                packet["space_pressure_hint"], packet["inode_pressure_hint"]
            ) else
            "NO_LOCAL_THRESHOLD_CROSSED"
            if packet["inode_status"] == "OBSERVED"
            else "UNKNOWN"
        )
    except (OSError, ValueError, OverflowError):
        pass
    return packet

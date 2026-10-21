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
        pressure = (
            "HIGH" if available < 2 * 1024**3 or unavailable >= 95
            else "ELEVATED" if available < 5 * 1024**3 or unavailable >= 90
            else "NO_LOCAL_THRESHOLD_CROSSED"
        )
        packet.update({
            "status": "ROOT_STATVFS_OBSERVED",
            "available_gib": round(available / 1024**3, 2),
            "unavailable_percent": round(unavailable, 1),
            "pressure_hint": pressure,
        })
    except (OSError, ValueError, OverflowError):
        pass
    return packet

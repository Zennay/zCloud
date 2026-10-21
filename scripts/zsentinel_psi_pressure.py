"""Read-only, bounded Linux PSI observations for zSentinel incident operators.

PSI average stall percentages complement load average but do not prove a
GitHub runner is blocked. No process control, claims, service writes or retries.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
import os
import re

MAX_BYTES = 2048
SOURCES = ("cpu", "memory", "io")
LINE = re.compile(
    r"^(some|full) avg10=([0-9]+(?:\.[0-9]+)?) "
    r"avg60=([0-9]+(?:\.[0-9]+)?) "
    r"avg300=([0-9]+(?:\.[0-9]+)?) total=([0-9]+)$"
)


def _read_psi(source):
    """Open only the fixed kernel sources, never user-supplied paths."""
    if source not in SOURCES:
        raise ValueError("unknown PSI source")
    fd = os.open(
        "/proc/pressure/" + source,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0),
    )
    try:
        raw = os.read(fd, MAX_BYTES + 1)
    finally:
        os.close(fd)
    if not raw or len(raw) > MAX_BYTES:
        raise ValueError("missing or oversized PSI source")
    rows = {}
    for row in raw.decode("ascii", "strict").strip().splitlines():
        match = LINE.fullmatch(row.strip())
        if match is None:
            raise ValueError("invalid PSI record")
        label = match.group(1)
        if label in rows:
            raise ValueError("duplicate PSI record")
        numbers = [float(value) for value in match.groups()[1:4]]
        if not all(math.isfinite(value) and 0 <= value <= 100 for value in numbers):
            raise ValueError("invalid stall percentage")
        rows[label] = {
            "avg10_pct": round(numbers[0], 2),
            "avg60_pct": round(numbers[1], 2),
            "avg300_pct": round(numbers[2], 2),
        }
    if "some" not in rows or (source in ("memory", "io") and "full" not in rows):
        raise ValueError("missing mandatory PSI state")
    # CPU full is absent or kernel-unsupported on some versions; never rely
    # on it for a severity decision even if present.
    return {key: rows[key] for key in ("some", "full") if key in rows}


def observe_psi():
    """Return source-limited PSI evidence; UNKNOWN must never become 0."""
    result = {
        "source": "local_linux_proc_pressure_avg10_avg60_avg300",
        "observed_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "UNKNOWN",
        "source_complete": False,
        "resources": {},
        "pressure_hint": "UNKNOWN",
        "job_blocked": "NOT_INFERABLE",
        "new_worker_authorized": False,
        "restart_authorized": False,
    }
    for source in SOURCES:
        try:
            result["resources"][source] = {
                "status": "OBSERVED",
                "measurements": _read_psi(source),
            }
        except (OSError, ValueError, UnicodeError, OverflowError):
            result["resources"][source] = {
                "status": "UNKNOWN",
                "measurements": None,
            }
    if not all(row["status"] == "OBSERVED" for row in result["resources"].values()):
        result["status"] = "PARTIAL"
        return result

    def average(source, label="some"):
        return result["resources"][source]["measurements"][label]["avg60_pct"]

    cpu_some = average("cpu")
    memory_some = average("memory")
    memory_full = average("memory", "full")
    io_some = average("io")
    io_full = average("io", "full")
    severity = (
        "HIGH" if (
            cpu_some >= 70 or memory_some >= 30 or memory_full >= 10
            or io_some >= 30 or io_full >= 10
        ) else
        "ELEVATED" if (
            cpu_some >= 25 or memory_some >= 5 or memory_full >= 1
            or io_some >= 5 or io_full >= 1
        ) else "NO_LOCAL_THRESHOLD_CROSSED"
    )
    result.update({
        "status": "OBSERVED",
        "source_complete": True,
        "pressure_hint": severity,
    })
    return result


def psi_sentence(packet):
    """Short read-only evidence summary for operator text/HTML/Markdown."""
    if not isinstance(packet, dict):
        return "PSI: NIET GEVRAAGD."
    if packet.get("status") != "OBSERVED":
        return "PSI: ONVOLLEDIG/ONBEKEND; kernelbron niet bruikbaar voor classificatie."
    resources = packet["resources"]
    def source_line(key):
        values = resources[key]["measurements"]
        return f"{key} some {values['some']['avg60_pct']}%" + (
            f", full {values['full']['avg60_pct']}%"
            if key != "cpu" else ""
        )
    return (
        "PSI (60s kernel-stallpercentages): " +
        "; ".join(source_line(key) for key in SOURCES)
        + f"; indicator {packet['pressure_hint']}. "
        "Geen bewijs van GitHub-jobstatus, cgroup-ruimte of herstartrecht."
    )

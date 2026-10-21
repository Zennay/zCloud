"""Bounded read-only host pressure observation for zSentinel's operator desk.

Never reserves CPU, adjusts cgroups, admits workers or infers loaded
scheduler configuration. Linux /proc reflects host, not necessarily all
cgroup/container or remote GitHub-runner limits.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
import os
from pathlib import Path
import re
from scripts.zsentinel_disk_pressure import observe_root_disk
from scripts.zsentinel_psi_pressure import observe_psi, psi_sentence

MAX_SOURCE_BYTES = 4096
MEM_FIELDS = ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree")
MEM_PATTERN = re.compile(r"^([A-Za-z_()]+):\s*([0-9]+) kB$")


def _proc_source(name):
    fd = os.open(
        str(Path("/proc") / name),
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0),
    )
    try:
        raw = os.read(fd, MAX_SOURCE_BYTES + 1)
    finally:
        os.close(fd)
    if not raw or len(raw) > MAX_SOURCE_BYTES:
        raise ValueError("proc source unavailable or over byte limit")
    return raw.decode("ascii", "strict")


def observe_host_pressure():
    """Return a deliberately incomplete, non-authorizing host snapshot.

    The four thresholds are *operator review hints*, not worker admission
    or service health decisions. Fail closed on malformed/partial sources.
    """
    report = {
        "observed_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "source": "linux_procfs_host_only",
        "status": "UNKNOWN",
        "host_memory_total_mib": None,
        "host_memory_available_mib": None,
        "host_memory_available_percent": None,
        "swap_used_percent": None,
        "load_one_per_cpu": None,
        "pressure_hint": "UNKNOWN",
        "scope": "HOST_ONLY_CGROUP_AND_OTHER_WORK_NOT_OBSERVED",
        "new_worker_authorized": False,
        "runtime_recovered": False,
        "root_disk": observe_root_disk(),
        "psi": observe_psi(),
    }
    try:
        rows = {}
        for line in _proc_source("meminfo").splitlines():
            match = MEM_PATTERN.fullmatch(line)
            if not match:
                continue
            key, value = match.groups()
            if key in MEM_FIELDS:
                if key in rows:
                    raise ValueError("duplicate meminfo entry")
                rows[key] = int(value)
        if any(key not in rows for key in MEM_FIELDS):
            raise ValueError("required meminfo fields absent")
        total = rows["MemTotal"]
        available = rows["MemAvailable"]
        swap_total = rows["SwapTotal"]
        swap_free = rows["SwapFree"]
        if not (0 < total <= 10**11 and 0 <= available <= total
                and 0 <= swap_total <= 10**11 and 0 <= swap_free <= swap_total):
            raise ValueError("invalid memory counters")
        load_parts = _proc_source("loadavg").split()
        if len(load_parts) < 3:
            raise ValueError("loadavg source incomplete")
        load = float(load_parts[0])
        cpus = os.cpu_count()
        if not (math.isfinite(load) and 0 <= load <= 100000
                and isinstance(cpus, int) and 1 <= cpus <= 1024):
            raise ValueError("invalid host load or core count")
        memory_available_pct = 100.0 * available / total
        swap_used_pct = 100.0 * (swap_total - swap_free) / swap_total if swap_total else 0.0
        one_per_cpu = load / cpus
        root_disk = report["root_disk"]
        disk_high = root_disk.get("pressure_hint") == "HIGH"
        disk_elevated = root_disk.get("pressure_hint") == "ELEVATED"
        psi = report["psi"]
        psi_high = psi.get("pressure_hint") == "HIGH"
        psi_elevated = psi.get("pressure_hint") == "ELEVATED"
        # Review hints only. No claim, RAM promise or admission decision.
        hint = (
            "HIGH_LOCAL_PRESSURE" if (
                memory_available_pct < 15 or
                (swap_total > 0 and swap_used_pct >= 90) or
                one_per_cpu >= 2 or disk_high or psi_high
            ) else
            "ELEVATED_LOCAL_PRESSURE" if (
                memory_available_pct < 25 or
                (swap_total > 0 and swap_used_pct >= 70) or
                one_per_cpu >= 1.25 or disk_elevated or psi_elevated
            ) else "NO_LOCAL_THRESHOLD_CROSSED"
        )
        report.update({
            "status": "HOST_SNAPSHOT_OBSERVED",
            "host_memory_total_mib": total // 1024,
            "host_memory_available_mib": available // 1024,
            "host_memory_available_percent": round(memory_available_pct, 1),
            "swap_used_percent": round(swap_used_pct, 1),
            "load_one_per_cpu": round(one_per_cpu, 2),
            "pressure_hint": hint,
        })
    except (OSError, ValueError, UnicodeError, OverflowError):
        return report
    return report


def pressure_sentence(report):
    """Concise, private operator-visible resource fact; no admission claim."""
    if report.get("status") != "HOST_SNAPSHOT_OBSERVED":
        return "Hostdruk: niet betrouwbaar waargenomen; worker-toelating ONBEKEND."
    disk = report.get("root_disk") or {}
    disk_line = (
        "root beschikbaar " + str(disk["available_gib"]) + " GiB; "
        + str(disk["unavailable_percent"]) + "% voor proces onbeschikbaar"
        if disk.get("status") == "ROOT_STATVFS_OBSERVED"
        else "root-schijfruimte ONBEKEND"
    )
    return (
        f"Hostdruk: {report['pressure_hint']}; "
        f"RAM beschikbaar {report['host_memory_available_mib']} MiB "
        f"({report['host_memory_available_percent']}%); "
        f"swap gebruikt {report['swap_used_percent']}%; "
        f"load/CPU {report['load_one_per_cpu']}; {disk_line}. "
        + psi_sentence(report.get("psi")) + " "
        "Alleen hostbron; geen cgroup-, claim- of nieuwe-worker-toelating."
    )

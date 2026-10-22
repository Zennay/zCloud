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
        "swap_status": "UNKNOWN",
        "load_one_per_cpu": None,
        "pressure_hint": "UNKNOWN",
        "pressure_basis": [],
        "pressure_scope": "UNKNOWN",
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
        swap_status = "OBSERVED" if swap_total else "NO_SWAP_CONFIGURED"
        swap_used_pct = (
            100.0 * (swap_total - swap_free) / swap_total
            if swap_total else None
        )
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
        basis = []
        if memory_available_pct < 15:
            basis.append("ram_available_high")
        elif memory_available_pct < 25:
            basis.append("ram_available_elevated")
        if swap_total > 0 and swap_used_pct is not None:
            if swap_used_pct >= 90:
                basis.append("swap_usage_high")
            elif swap_used_pct >= 70:
                basis.append("swap_usage_elevated")
        if one_per_cpu >= 2:
            basis.append("load_per_cpu_high")
        elif one_per_cpu >= 1.25:
            basis.append("load_per_cpu_elevated")
        if disk_high:
            basis.append("root_disk_high")
        elif disk_elevated:
            basis.append("root_disk_elevated")
        if psi.get("pressure_hint") in ("HIGH", "ELEVATED"):
            psi_level = psi["pressure_hint"].lower()
            psi_sources = [
                item for item in psi.get("pressure_basis", [])
                if item in ("cpu", "memory", "io")
            ]
            basis.extend(f"psi_{item}_{psi_level}" for item in psi_sources)
            if not psi_sources:
                basis.append("psi_pressure_" + psi_level)
        report.update({
            "status": "HOST_SNAPSHOT_OBSERVED",
            "host_memory_total_mib": total // 1024,
            "host_memory_available_mib": available // 1024,
            "host_memory_available_percent": round(memory_available_pct, 1),
            "swap_used_percent": (
                round(swap_used_pct, 1) if swap_used_pct is not None else None
            ),
            "swap_status": swap_status,
            "load_one_per_cpu": round(one_per_cpu, 2),
            "pressure_hint": hint,
            "pressure_basis": basis,
            "pressure_scope": "HOST_METRICS_AND_KNOWN_KERNEL_SOURCES",
        })
    except (OSError, ValueError, UnicodeError, OverflowError):
        # A transient/malformed meminfo or loadavg must not erase real severe
        # kernel PSI or disk evidence. Remain explicitly PARTIAL: neither
        # missing RAM data nor missing CPU count may be treated as healthy.
        disk_hint = report["root_disk"].get("pressure_hint")
        psi_hint = report["psi"].get("pressure_hint")
        if disk_hint != "UNKNOWN" or psi_hint != "UNKNOWN":
            report["status"] = "HOST_PARTIAL"
            basis = []
            if disk_hint == "HIGH":
                basis.append("root_disk_high")
            elif disk_hint == "ELEVATED":
                basis.append("root_disk_elevated")
            if psi_hint in ("HIGH", "ELEVATED"):
                psi_level = psi_hint.lower()
                psi_sources = [
                    item for item in (report["psi"].get("pressure_basis") or [])
                    if item in ("cpu", "memory", "io")
                ]
                basis.extend(f"psi_{item}_{psi_level}" for item in psi_sources)
                if not psi_sources:
                    basis.append("psi_pressure_" + psi_level)
            report["pressure_basis"] = basis
            report["pressure_scope"] = "OBSERVED_DISK_AND_PSI_ONLY"
            if disk_hint == "HIGH" or psi_hint == "HIGH":
                report["pressure_hint"] = "HIGH_LOCAL_PRESSURE"
            elif disk_hint == "ELEVATED" or psi_hint == "ELEVATED":
                report["pressure_hint"] = "ELEVATED_LOCAL_PRESSURE"
        return report
    return report


def pressure_sentence(report):
    """Concise, private operator-visible resource fact; no admission claim."""
    def basis_text():
        labels = {
            "ram_available_high": "RAM-beschikbaarheid <15%",
            "ram_available_elevated": "RAM-beschikbaarheid <25%",
            "swap_usage_high": "swapgebruik ≥90%",
            "swap_usage_elevated": "swapgebruik ≥70%",
            "load_per_cpu_high": "load/CPU ≥2",
            "load_per_cpu_elevated": "load/CPU ≥1,25",
            "root_disk_high": "root-schijfdruk hoog",
            "root_disk_elevated": "root-schijfdruk verhoogd",
            "psi_cpu_high": "PSI CPU-druk hoog",
            "psi_cpu_elevated": "PSI CPU-druk verhoogd",
            "psi_memory_high": "PSI geheugendruk hoog",
            "psi_memory_elevated": "PSI geheugendruk verhoogd",
            "psi_io_high": "PSI I/O-druk hoog",
            "psi_io_elevated": "PSI I/O-druk verhoogd",
            "psi_pressure_high": "PSI-druk hoog (bronbasis beperkt)",
            "psi_pressure_elevated": "PSI-druk verhoogd (bronbasis beperkt)",
        }
        items = [
            labels.get(code, "drukbron onbekend")
            for code in report.get("pressure_basis", [])
            if isinstance(code, str)
        ]
        return (
            "Drempelbronnen: " + ", ".join(items) + ". "
            if items else
            "Geen drempelbron bevestigd; ontbrekende bronnen kunnen druk verbergen. "
            if report.get("status") != "HOST_SNAPSHOT_OBSERVED" else
            "Geen lokale drempelbron overschreden. "
        )

    if report.get("status") != "HOST_SNAPSHOT_OBSERVED":
        disk = report.get("root_disk") or {}
        disk_note = (
            f"root beschikbaar {disk['available_gib']} GiB, "
            f"{disk['unavailable_percent']}% onbeschikbaar"
            if disk.get("status") == "ROOT_STATVFS_OBSERVED"
            else "root-schijfruimte onbekend"
        )
        return (
            f"Hostdruk: PARTIËLE BRON ({report.get('pressure_hint', 'UNKNOWN')}); "
            "RAM/load onbekend; " + disk_note + ". "
            + basis_text() + psi_sentence(report.get("psi"))
            + " Geen autorisatie voor nieuwe workers of herstarts."
        )
    disk = report.get("root_disk") or {}
    disk_line = (
        "root beschikbaar " + str(disk["available_gib"]) + " GiB; "
        + str(disk["unavailable_percent"]) + "% voor proces onbeschikbaar"
        if disk.get("status") == "ROOT_STATVFS_OBSERVED"
        else "root-schijfruimte ONBEKEND"
    )
    swap_line = (
        f"swap gebruikt {report['swap_used_percent']}%"
        if report.get("swap_status") == "OBSERVED"
        else "swap niet geconfigureerd"
        if report.get("swap_status") == "NO_SWAP_CONFIGURED"
        else "swapgebruik ONBEKEND"
    )
    return (
        f"Hostdruk: {report['pressure_hint']}; "
        f"RAM beschikbaar {report['host_memory_available_mib']} MiB "
        f"({report['host_memory_available_percent']}%); "
        + swap_line + "; "
        f"load/CPU {report['load_one_per_cpu']}; {disk_line}. "
        + basis_text() + psi_sentence(report.get("psi")) + " "
        "Alleen hostbron; geen cgroup-, claim- of nieuwe-worker-toelating."
    )

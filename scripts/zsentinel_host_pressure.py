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
    """Return bounded per-source host evidence; partial data never means healthy."""
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
    root_disk = report["root_disk"]
    psi = report["psi"]
    psi_status = psi.get("status")
    coverage = {
        "meminfo": "UNKNOWN",
        "loadavg": "UNKNOWN",
        "root_disk": (
            "OBSERVED"
            if (root_disk.get("status") == "ROOT_STATVFS_OBSERVED"
                and root_disk.get("inode_status") == "OBSERVED")
            else "PARTIAL"
            if root_disk.get("status") == "ROOT_STATVFS_OBSERVED"
            else "UNKNOWN"
        ),
        "psi": psi_status if psi_status in ("OBSERVED", "PARTIAL") else "UNKNOWN",
    }
    memory_available_pct = None
    swap_used_pct = None
    one_per_cpu = None

    # Read RAM/swap independently from loadavg. A broken loadavg must not
    # erase correctly observed memory, swap, disk or PSI evidence.
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
        memory_available_pct = 100.0 * available / total
        swap_status = "OBSERVED" if swap_total else "NO_SWAP_CONFIGURED"
        swap_used_pct = (
            100.0 * (swap_total - swap_free) / swap_total
            if swap_total else None
        )
        report.update({
            "host_memory_total_mib": total // 1024,
            "host_memory_available_mib": available // 1024,
            "host_memory_available_percent": round(memory_available_pct, 1),
            "swap_used_percent": (
                round(swap_used_pct, 1) if swap_used_pct is not None else None
            ),
            "swap_status": swap_status,
        })
        coverage["meminfo"] = "OBSERVED"
    except (OSError, ValueError, UnicodeError, OverflowError):
        pass

    # Load is its own source; keep a failed/partial read explicitly UNKNOWN.
    try:
        load_parts = _proc_source("loadavg").split()
        if len(load_parts) < 3:
            raise ValueError("loadavg source incomplete")
        load = float(load_parts[0])
        cpus = os.cpu_count()
        if not (math.isfinite(load) and 0 <= load <= 100000
                and isinstance(cpus, int) and 1 <= cpus <= 1024):
            raise ValueError("invalid host load or core count")
        one_per_cpu = load / cpus
        report["load_one_per_cpu"] = round(one_per_cpu, 2)
        coverage["loadavg"] = "OBSERVED"
    except (OSError, ValueError, UnicodeError, OverflowError):
        pass

    basis = []
    high = False
    elevated = False
    if memory_available_pct is not None:
        if memory_available_pct < 15:
            high = True
            basis.append("ram_available_high")
        elif memory_available_pct < 25:
            elevated = True
            basis.append("ram_available_elevated")
    if swap_used_pct is not None:
        if swap_used_pct >= 90:
            high = True
            basis.append("swap_usage_high")
        elif swap_used_pct >= 70:
            elevated = True
            basis.append("swap_usage_elevated")
    if one_per_cpu is not None:
        if one_per_cpu >= 2:
            high = True
            basis.append("load_per_cpu_high")
        elif one_per_cpu >= 1.25:
            elevated = True
            basis.append("load_per_cpu_elevated")

    disk_hint = root_disk.get("space_pressure_hint", root_disk.get("pressure_hint"))
    if disk_hint == "HIGH":
        high = True
        basis.append("root_disk_high")
    elif disk_hint == "ELEVATED":
        elevated = True
        basis.append("root_disk_elevated")
    inode_hint = root_disk.get("inode_pressure_hint")
    if inode_hint == "HIGH":
        high = True
        basis.append("root_inode_high")
    elif inode_hint == "ELEVATED":
        elevated = True
        basis.append("root_inode_elevated")

    if psi.get("pressure_hint") in ("HIGH", "ELEVATED"):
        psi_level = psi["pressure_hint"].lower()
        psi_sources = [
            item for item in (psi.get("pressure_basis") or [])
            if item in ("cpu", "memory", "io")
        ]
        basis.extend(f"psi_{item}_{psi_level}" for item in psi_sources)
        if not psi_sources:
            basis.append("psi_pressure_" + psi_level)
        if psi["pressure_hint"] == "HIGH":
            high = True
        else:
            elevated = True

    sources_complete = all(value == "OBSERVED" for value in coverage.values())
    any_source_observed = any(
        value in ("OBSERVED", "PARTIAL") for value in coverage.values()
    )
    hint = (
        "HIGH_LOCAL_PRESSURE" if high else
        "ELEVATED_LOCAL_PRESSURE" if elevated else
        "NO_LOCAL_THRESHOLD_CROSSED" if sources_complete else
        "UNKNOWN"
    )
    report.update({
        "status": (
            "HOST_SNAPSHOT_OBSERVED" if sources_complete else
            "HOST_PARTIAL" if any_source_observed else "UNKNOWN"
        ),
        "pressure_hint": hint,
        "pressure_basis": basis,
        "pressure_scope": (
            "HOST_METRICS_AND_KERNEL_SOURCES"
            if sources_complete else "OBSERVED_SOURCES_ONLY"
        ),
        "source_coverage": coverage,
        "observed_sources": sorted(
            key for key, value in coverage.items() if value == "OBSERVED"
        ),
        "partial_sources": sorted(
            key for key, value in coverage.items() if value == "PARTIAL"
        ),
        "missing_sources": sorted(
            key for key, value in coverage.items() if value == "UNKNOWN"
        ),
    })
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
            "root_disk_high": "root-schijfruimtedruk hoog",
            "root_disk_elevated": "root-schijfruimtedruk verhoogd",
            "root_inode_high": "root-inodedruk hoog",
            "root_inode_elevated": "root-inodedruk verhoogd",
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

    disk = report.get("root_disk") or {}
    disk_note = (
        f"root beschikbaar {disk['available_gib']} GiB, "
        f"{disk['unavailable_percent']}% blokken niet beschikbaar"
        if disk.get("status") == "ROOT_STATVFS_OBSERVED"
        else "root-schijfruimte onbekend"
    )
    inode_note = (
        f"inodes beschikbaar {disk['available_inodes']}, "
        f"{disk['unavailable_inode_percent']}% niet beschikbaar "
        f"({disk['inode_pressure_hint']})"
        if disk.get("inode_status") == "OBSERVED"
        else "inodecapaciteit UNKNOWN"
    )
    disk_note += "; " + inode_note
    swap_line = (
        f"swap gebruikt {report['swap_used_percent']}%"
        if report.get("swap_status") == "OBSERVED"
        else "swap niet geconfigureerd"
        if report.get("swap_status") == "NO_SWAP_CONFIGURED"
        else "swapgebruik onbekend"
    )
    memory_line = (
        f"RAM beschikbaar {report['host_memory_available_mib']} MiB "
        f"({report['host_memory_available_percent']}%)"
        if report.get("host_memory_available_mib") is not None
        else "RAM-beschikbaarheid onbekend"
    )
    load_line = (
        f"load/CPU {report['load_one_per_cpu']}"
        if report.get("load_one_per_cpu") is not None
        else "load/CPU onbekend"
    )

    if report.get("status") != "HOST_SNAPSHOT_OBSERVED":
        labels = {
            "meminfo": "RAM/swap", "loadavg": "load/CPU",
            "root_disk": "root-schijfruimte/inodes", "psi": "PSI",
        }
        coverage = report.get("source_coverage") or {}
        incomplete = [
            labels.get(key, key) + "=" + value
            for key, value in sorted(coverage.items())
            if value != "OBSERVED"
        ]
        coverage_note = (
            "Brondekking onvolledig: " + ", ".join(incomplete) + ". "
            if incomplete else "Brondekking onbekend. "
        )
        status_label = (
            "PARTIËLE BRON" if report.get("status") == "HOST_PARTIAL"
            else "ONBEKEND"
        )
        return (
            f"Hostdruk: {status_label} ({report.get('pressure_hint', 'UNKNOWN')}); "
            + memory_line + "; " + swap_line + "; " + load_line + "; "
            + disk_note + ". " + coverage_note + basis_text()
            + psi_sentence(report.get("psi"))
            + " Geen autorisatie voor nieuwe workers of herstarts."
        )
    return (
        f"Hostdruk: {report['pressure_hint']}; "
        + memory_line + "; " + swap_line + "; " + load_line + "; "
        + disk_note + ". " + basis_text()
        + psi_sentence(report.get("psi")) + " "
        "Alleen hostbron; geen cgroup-, claim- of nieuwe-worker-toelating."
    )
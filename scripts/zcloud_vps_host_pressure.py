"""Bounded read-only Linux CPU and pressure evidence for shared-VPS release handoff.

This module makes no process/queue/service changes and grants no release authority.
"""
from __future__ import annotations

import os
from pathlib import Path


def _psi_avg10(resource: str, category: str) -> float | None:
    """Read only the aggregate ten-second percentage, not process details."""
    path = Path("/proc/pressure") / resource
    try:
        lines = path.read_text(encoding="ascii")[:2048].splitlines()
    except (OSError, UnicodeError):
        return None
    for line in lines[:4]:
        parts = line.split()
        if not parts or parts[0] != category:
            continue
        for token in parts[1:]:
            if not token.startswith("avg10="):
                continue
            try:
                value = float(token[6:])
            except ValueError:
                return None
            if value != value or value < 0 or value > 100:
                return None
            return round(value, 2)
    return None


def host_pressure_snapshot() -> dict:
    """Observe CPU demand and kernel PSI without guessing worker or runner state.

    One-minute load is runnable/uninterruptible demand, NOT CPU utilisation.
    PSI is host-level contention, not proof of which workload caused it.
    """
    result = {
        "source": "local_procfs", "state": "unknown",
        "cpu_count": None, "load_1m": None, "load_per_cpu": None,
        "cpu_some_avg10_pct": _psi_avg10("cpu", "some"),
        "memory_full_avg10_pct": _psi_avg10("memory", "full"),
        "io_full_avg10_pct": _psi_avg10("io", "full"),
        "worker_attribution_verified": False,
        "release_authorized": False,
        "changes_host": False,
    }
    try:
        cpus = os.cpu_count()
        load = os.getloadavg()[0]
    except (OSError, ValueError):
        cpus, load = None, None
    if not isinstance(cpus, int) or cpus < 1 or cpus > 4096 or load is None or load < 0 or load != load or load > 1e6:
        return result
    result["cpu_count"] = cpus
    result["load_1m"] = round(load, 2)
    result["load_per_cpu"] = round(load / cpus, 2)
    psi = (result["cpu_some_avg10_pct"], result["memory_full_avg10_pct"],
           result["io_full_avg10_pct"])
    if any(x is None for x in psi):
        result["state"] = "partial"
    else:
        result["state"] = "observed"
    return result


def pressure_findings(sample: dict) -> list[dict[str, str]]:
    """Conservative operator signals only; never infer release clearance."""
    findings: list[dict[str, str]] = []
    ratio = sample["load_per_cpu"]
    if ratio is None:
        findings.append({"code": "HOST_PRESSURE_INCOMPLETE", "severity": "warn",
                         "detail": "Unable to observe CPU demand; no host-headroom assertion."})
        return findings
    if ratio >= 2.0:
        findings.append({"code": "HOST_CPU_SATURATED", "severity": "block",
                         "detail": "One-minute runnable/I/O demand at least 2x logical CPU count; preserve active jobs."})
    elif ratio >= 1.0:
        findings.append({"code": "HOST_CPU_BUSY", "severity": "warn",
                         "detail": "One-minute demand at least logical CPU count; inspect concurrent workloads before release."})
    memory = sample["memory_full_avg10_pct"]
    io = sample["io_full_avg10_pct"]
    if memory is not None and memory >= 10:
        findings.append({"code": "HOST_MEMORY_STALL", "severity": "block",
                         "detail": "Memory PSI full stall >=10% over ten seconds; avoid overlapping heavy jobs."})
    if io is not None and io >= 10:
        findings.append({"code": "HOST_IO_STALL", "severity": "block",
                         "detail": "I/O PSI full stall >=10% over ten seconds; avoid competing disk-heavy jobs."})
    if sample["state"] == "partial":
        findings.append({"code": "HOST_PRESSURE_INCOMPLETE", "severity": "warn",
                         "detail": "Kernel PSI coverage incomplete; missing counters are not zero pressure."})
    return findings

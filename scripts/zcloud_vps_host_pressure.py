"""Bounded read-only Linux CPU and pressure evidence for shared-VPS release handoff.

This module makes no process/queue/service changes and grants no release authority.
"""
from __future__ import annotations

import math
import os
from pathlib import Path

MAX_PSI_BYTES = 2048


def _psi_avg10(resource: str, category: str) -> float | None:
    """Read a bounded amount of aggregate ten-second PSI, not process details."""
    path = Path("/proc/pressure") / resource
    try:
        with path.open("rb") as source:
            raw = source.read(MAX_PSI_BYTES + 1)
        if len(raw) > MAX_PSI_BYTES:
            return None
        lines = raw.decode("ascii").splitlines()
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
    """Conservative independent pressure signals; never infer release clearance."""
    findings: list[dict[str, str]] = []
    if not isinstance(sample, dict):
        return [{"code": "HOST_PRESSURE_INCOMPLETE", "severity": "warn",
                 "detail": "Host-pressure snapshot is unavailable; no headroom or clearance assertion."}]

    ratio = sample.get("load_per_cpu")
    if (type(ratio) not in (int, float) or not math.isfinite(ratio)
            or ratio < 0 or ratio > 1e6):
        findings.append({"code": "HOST_PRESSURE_INCOMPLETE", "severity": "warn",
                         "detail": "Unable to observe CPU demand; no host-headroom assertion."})
    elif ratio >= 2.0:
        findings.append({"code": "HOST_CPU_SATURATED", "severity": "block",
                         "detail": "One-minute runnable/I/O demand at least 2x logical CPU count; preserve active jobs."})
    elif ratio >= 1.0:
        findings.append({"code": "HOST_CPU_BUSY", "severity": "warn",
                         "detail": "One-minute demand at least logical CPU count; inspect concurrent workloads before release."})

    # CPU/load availability is independent from kernel PSI. Do not return early:
    # a missing load average must never hide a real memory or I/O stall.
    for field, code, detail in (
        ("memory_full_avg10_pct", "HOST_MEMORY_STALL",
         "Memory PSI full stall >=10% over ten seconds; avoid overlapping heavy jobs."),
        ("io_full_avg10_pct", "HOST_IO_STALL",
         "I/O PSI full stall >=10% over ten seconds; avoid competing disk-heavy jobs."),
    ):
        value = sample.get(field)
        if (type(value) in (int, float) and math.isfinite(value)
                and 10 <= value <= 100):
            findings.append({"code": code, "severity": "block", "detail": detail})

    if (sample.get("state") in ("partial", "unknown")
            and not any(item["code"] == "HOST_PRESSURE_INCOMPLETE" for item in findings)):
        findings.append({"code": "HOST_PRESSURE_INCOMPLETE", "severity": "warn",
                         "detail": "Kernel PSI or CPU-demand coverage incomplete; missing counters are not zero pressure."})
    return findings

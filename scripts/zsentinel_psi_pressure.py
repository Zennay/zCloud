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
        "observed_sources": [],
        "pressure_basis": [],
        "pressure_scope": "UNKNOWN",
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
    observed = {
        source: row["measurements"]
        for source, row in result["resources"].items()
        if row.get("status") == "OBSERVED" and isinstance(row.get("measurements"), dict)
    }
    result["observed_sources"] = sorted(observed)

    def average(source, label="some"):
        return observed[source][label]["avg60_pct"]

    def source_pressure(source, measurements):
        some = measurements["some"]["avg60_pct"]
        full = measurements.get("full", {}).get("avg60_pct")
        if source == "cpu":
            high = some >= 70
            elevated = some >= 25
        else:
            high = some >= 30 or (full is not None and full >= 10)
            elevated = some >= 5 or (full is not None and full >= 1)
        return "HIGH" if high else "ELEVATED" if elevated else "NO_LOCAL_THRESHOLD_CROSSED"

    if len(observed) != len(SOURCES):
        high_sources = sorted(
            source for source, measurements in observed.items()
            if source_pressure(source, measurements) == "HIGH"
        )
        elevated_sources = sorted(
            source for source, measurements in observed.items()
            if source_pressure(source, measurements) == "ELEVATED"
        )
        result["status"] = "PARTIAL"
        result["source_complete"] = False
        result["pressure_scope"] = "OBSERVED_SOURCES_ONLY"
        # A missing PSI source cannot erase a severe observation from a known
        # source. Conversely, absent threshold crossings in partial data are
        # not evidence of low pressure: retain UNKNOWN.
        if high_sources:
            result["pressure_hint"] = "HIGH"
            result["pressure_basis"] = high_sources
        elif elevated_sources:
            result["pressure_hint"] = "ELEVATED"
            result["pressure_basis"] = elevated_sources
        return result

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
    if severity == "HIGH":
        result["pressure_basis"] = sorted(
            source for source, measurements in observed.items()
            if source_pressure(source, measurements) == "HIGH"
        )
    elif severity == "ELEVATED":
        result["pressure_basis"] = sorted(
            source for source, measurements in observed.items()
            if source_pressure(source, measurements) == "ELEVATED"
        )
    result["pressure_scope"] = "ALL_REQUIRED_SOURCES"
    window_contrast = {}
    all_differences = []
    for source in SOURCES:
        data = result["resources"][source]["measurements"]
        differences = {}
        for label in ("some", "full"):
            if label not in data or (source == "cpu" and label == "full"):
                continue
            # This compares kernel moving averages; it is NOT a historical
            # measurement or proof that the pressure will keep rising.
            change = round(data[label]["avg10_pct"] - data[label]["avg300_pct"], 2)
            differences[label + "_delta_pp"] = change
            all_differences.append(change)
        window_contrast[source] = differences
    signal = (
        "MIXED_WINDOWS" if (
            any(x >= 15 for x in all_differences)
            and any(x <= -15 for x in all_differences)
        ) else "SHORT_WINDOW_PRESSURE_HIGHER" if any(
            x >= 15 for x in all_differences
        ) else "SHORT_WINDOW_PRESSURE_LOWER" if any(
            x <= -15 for x in all_differences
        ) else "NO_LARGE_WINDOW_DIFFERENCE"
    )
    result.update({
        "status": "OBSERVED",
        "source_complete": True,
        "pressure_hint": severity,
        "window_contrast": window_contrast,
        "window_signal": signal,
    })
    return result


def psi_sentence(packet):
    """Short read-only evidence summary for operator text/HTML/Markdown."""
    if not isinstance(packet, dict):
        return "PSI: NIET GEVRAAGD."
    resources = packet.get("resources") or {}

    def source_line(key):
        detail = resources.get(key) or {}
        values = detail.get("measurements") or {}
        some = values.get("some") or {}
        full = values.get("full") or {}
        if detail.get("status") != "OBSERVED":
            return key + " UNKNOWN"
        return f"{key} some {some.get('avg60_pct')}%" + (
            f", full {full.get('avg60_pct')}%" if key != "cpu" else ""
        )

    if packet.get("status") == "PARTIAL":
        visible = [source_line(key) for key in SOURCES]
        hint = packet.get("pressure_hint", "UNKNOWN")
        conclusion = (
            f"bekende bronnen tonen {hint}"
            if hint in ("HIGH", "ELEVATED") else
            "geen drukdrempel aangetoond in bekende bronnen; ontbrekende bronnen kunnen druk verbergen"
        )
        return (
            "PSI PARTIEEL: " + conclusion + "; "
            + "; ".join(visible) + ". "
            "Ontbrekende bronnen blijven UNKNOWN; dit is geen volledige hostmeting "
            "of bewijs van GitHub-jobstatus, cgroup-ruimte of herstartrecht."
        )
    if packet.get("status") != "OBSERVED":
        return "PSI: ONVOLLEDIG/ONBEKEND; kernelbron niet bruikbaar voor classificatie."
    return (
        "PSI (60s kernel-stallpercentages): " +
        "; ".join(source_line(key) for key in SOURCES)
        + f"; indicator {packet['pressure_hint']}; "
        + f"10s versus 300s: {packet.get('window_signal', 'UNKNOWN')}. "
        "Dit is geen voorspelling of bewijs van GitHub-jobstatus, cgroup-ruimte of herstartrecht."
    )

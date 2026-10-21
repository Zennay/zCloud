"""Read-only, non-authorizing shared-host pressure trend from two private snapshots."""
from __future__ import annotations

import math

METRICS = (
    ("load_per_cpu", 0.5, "One-minute load per logical CPU"),
    ("cpu_some_avg10_pct", 5.0, "CPU PSI some, ten-second %"),
    ("memory_full_avg10_pct", 5.0, "Memory PSI full, ten-second %"),
    ("io_full_avg10_pct", 5.0, "I/O PSI full, ten-second %"),
)


def _valid_number(value, ceiling: float) -> float | None:
    if type(value) not in (int, float) or not math.isfinite(value):
        return None
    if not 0 <= value <= ceiling:
        return None
    return float(value)


def compare_host_pressure(previous: object, current: object,
                          elapsed_seconds: float) -> dict:
    """Operator-provided source, not a trusted continuous sensor feed.

    A missing/invalid source is never converted to recovery or CPU clearance.
    """
    result = {
        "status": "unavailable",
        "trend": "unknown",
        "elapsed_seconds": round(elapsed_seconds),
        "source": "operator_supplied_local_snapshots_unverified",
        "metrics": [],
        "release_authorized": False,
    }
    if current is None:
        result["status"] = "not_measured_currently"
        return result
    if previous is None:
        result["status"] = "not_measured_previously"
        return result
    if not isinstance(previous, dict) or not isinstance(current, dict):
        result["status"] = "untrusted_source"
        return result
    if (previous.get("source") != "local_procfs" or
            current.get("source") != "local_procfs" or
            previous.get("release_authorized") is not False or
            current.get("release_authorized") is not False):
        result["status"] = "untrusted_source"
        return result
    if not 0 <= elapsed_seconds <= 3600:
        result["status"] = "stale_or_inverted_window"
        return result
    before_cpus, after_cpus = previous.get("cpu_count"), current.get("cpu_count")
    if (type(before_cpus) is not int or type(after_cpus) is not int or
            not 1 <= before_cpus <= 4096 or before_cpus != after_cpus):
        result["status"] = "different_or_unknown_cpu_capacity"
        return result
    rising = falling = 0
    for field, threshold, label in METRICS:
        ceiling = 1e6 if field == "load_per_cpu" else 100
        start = _valid_number(previous.get(field), ceiling)
        end = _valid_number(current.get(field), ceiling)
        if start is None or end is None:
            continue
        delta = end - start
        direction = ("rising" if delta >= threshold else
                     "falling" if delta <= -threshold else "stable")
        rising += direction == "rising"
        falling += direction == "falling"
        result["metrics"].append({
            "metric": field, "label": label, "previous": round(start, 2),
            "current": round(end, 2), "delta": round(delta, 2),
            "direction": direction,
        })
    if not result["metrics"]:
        result["status"] = "incomplete_counter_overlap"
        return result
    result["status"] = "operator_snapshot_comparison_only"
    result["trend"] = ("mixed" if rising and falling else
                       "rising" if rising else "falling" if falling else "stable")
    return result

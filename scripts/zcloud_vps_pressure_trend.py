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
    # Check integers against bounds before float conversion; hostile or
    # malformed arbitrarily large JSON integers must not overflow math.isfinite.
    if type(value) is int:
        if not 0 <= value <= ceiling:
            return None
        return float(value)
    if type(value) is not float or not math.isfinite(value):
        return None
    if not 0 <= value <= ceiling:
        return None
    return value


def _consistent_cpu_load(snapshot: dict, label: str) -> tuple[float | None, list[str]]:
    gaps: list[str] = []
    cpus = snapshot.get("cpu_count")
    if type(cpus) is not int or not 1 <= cpus <= 4096:
        gaps.append(label + ".cpu_count")
        return None, gaps
    load = _valid_number(snapshot.get("load_1m"), 1e6)
    ratio = _valid_number(snapshot.get("load_per_cpu"), 1e6)
    if load is None:
        gaps.append(label + ".load_1m")
    if ratio is None:
        gaps.append(label + ".load_per_cpu")
    if load is None or ratio is None:
        return None, gaps
    if abs(ratio - load / cpus) > 0.02:
        gaps.append(label + ".load_per_cpu_mismatch")
        return None, gaps
    return ratio, gaps


def compare_host_pressure(previous: object, current: object,
                          elapsed_seconds: object) -> dict:
    """Operator-provided source, not a trusted continuous sensor feed.

    Missing, malformed or partial inputs never become an overall trend or
    recovery/CPU-clearance assertion.
    """
    elapsed_valid = (
        type(elapsed_seconds) in (int, float) and
        (type(elapsed_seconds) is int or math.isfinite(elapsed_seconds)) and
        0 < elapsed_seconds <= 3600
    )
    result = {
        "status": "unavailable",
        "trend": "unknown",
        "elapsed_seconds": round(elapsed_seconds) if elapsed_valid else None,
        "source": "operator_supplied_local_snapshots_unverified",
        "metrics": [],
        "metrics_compared": 0,
        "metrics_expected": len(METRICS),
        "coverage": "unknown",
        "source_gaps": [],
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
        result["source_gaps"] = ["previous_or_current_snapshot_shape"]
        return result
    fingerprints = []
    for label, sample in (("previous", previous), ("current", current)):
        if sample.get("source") != "local_procfs":
            result["source_gaps"].append(label + ".source")
        if sample.get("release_authorized") is not False:
            result["source_gaps"].append(label + ".release_authorized")
        if sample.get("changes_host") is not False:
            result["source_gaps"].append(label + ".changes_host")
        fingerprint = sample.get("source_scope_fingerprint")
        if (type(fingerprint) is not str or len(fingerprint) != 64
                or any(char not in "0123456789abcdef" for char in fingerprint)):
            result["source_gaps"].append(label + ".source_scope_fingerprint")
        fingerprints.append(fingerprint if isinstance(fingerprint, str) else None)
    if result["source_gaps"]:
        result["status"] = "untrusted_source"
        return result
    if fingerprints[0] != fingerprints[1]:
        result["status"] = "different_source_scope"
        result["source_gaps"] = ["source_scope_mismatch"]
        return result
    if not elapsed_valid:
        result["status"] = "stale_or_inverted_window"
        result["source_gaps"] = ["elapsed_seconds"]
        return result

    for label, sample in (("previous", previous), ("current", current)):
        if sample.get("state") != "observed":
            result["source_gaps"].append(label + ".state_not_observed")

    before_cpus, after_cpus = previous.get("cpu_count"), current.get("cpu_count")
    if (type(before_cpus) is not int or type(after_cpus) is not int or
            not 1 <= before_cpus <= 4096 or not 1 <= after_cpus <= 4096 or
            before_cpus != after_cpus):
        if type(before_cpus) is not int or not 1 <= before_cpus <= 4096:
            result["source_gaps"].append("previous.cpu_count")
        if type(after_cpus) is not int or not 1 <= after_cpus <= 4096:
            result["source_gaps"].append("current.cpu_count")
        if (type(before_cpus) is int and type(after_cpus) is int and
                1 <= before_cpus <= 4096 and 1 <= after_cpus <= 4096 and
                before_cpus != after_cpus):
            result["source_gaps"].append("cpu_capacity_changed")
        result["status"] = "different_or_unknown_cpu_capacity"
        return result

    before_load, before_gaps = _consistent_cpu_load(previous, "previous")
    after_load, after_gaps = _consistent_cpu_load(current, "current")
    result["source_gaps"].extend(before_gaps + after_gaps)

    rising = falling = 0
    for field, threshold, label in METRICS:
        if field == "load_per_cpu":
            start, end = before_load, after_load
        else:
            start = _valid_number(previous.get(field), 100)
            end = _valid_number(current.get(field), 100)
            if start is None:
                result["source_gaps"].append("previous." + field)
            if end is None:
                result["source_gaps"].append("current." + field)
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

    result["metrics_compared"] = len(result["metrics"])
    result["coverage"] = (
        "complete" if len(result["metrics"]) == len(METRICS)
        and not result["source_gaps"] else
        "partial" if result["metrics"] else "none"
    )
    if not result["metrics"]:
        result["status"] = "incomplete_counter_overlap"
        return result
    if result["coverage"] != "complete":
        result["status"] = "partial_counter_overlap"
        # Per-metric deltas remain available, but an aggregate trend is withheld.
        result["trend"] = "unknown"
        return result

    result["status"] = "operator_snapshot_comparison_only"
    result["trend"] = ("mixed" if rising and falling else
                       "rising" if rising else "falling" if falling else "stable")
    return result

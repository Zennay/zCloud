#!/usr/bin/env python3
"""Read-only host pressure classifier for the zCloud control plane.

The classifier deliberately separates compute saturation from memory/IO
pressure so a busy CPU is not automatically treated as a control-plane
incident. It reads only aggregate Linux /proc counters; no process argv,
environment or per-process metadata is inspected.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

STATE_CONTRACT = (
    "healthy",
    "compute_busy",
    "memory_pressure",
    "io_pressure",
    "mixed_pressure",
)

LOAD_PER_CORE_BUSY = 1.0
CPU_PSI_SOME_BUSY = 20.0
MEM_AVAILABLE_LOW_PCT = 10.0
MEM_PSI_SOME_PRESSURE = 5.0
MEM_PSI_FULL_PRESSURE = 1.0
IO_PSI_SOME_PRESSURE = 10.0
IO_PSI_FULL_PRESSURE = 1.0
SWAP_FREE_LOW_PCT = 5.0

_CPU_LINE_RE = re.compile(r"^cpu\d+\s")


def _read_required(path: Path) -> str:
    if path.is_symlink():
        raise ValueError(f"symlink_not_allowed:{path.name}")
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"unreadable:{path.name}") from exc


def _ratio_pct(numerator: float, denominator: float) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator * 100.0, 2)


def _parse_meminfo(text: str) -> dict:
    values: dict[str, int] = {}
    for raw in text.splitlines():
        if ":" not in raw:
            continue
        key, rest = raw.split(":", 1)
        parts = rest.strip().split()
        if not parts:
            continue
        try:
            values[key] = int(parts[0])
        except ValueError:
            continue
    required = {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}
    missing = sorted(required - values.keys())
    if missing:
        raise ValueError("meminfo_missing:" + ",".join(missing))
    if values["MemTotal"] <= 0 or values["MemAvailable"] < 0:
        raise ValueError("meminfo_invalid")
    if values["SwapTotal"] < 0 or values["SwapFree"] < 0:
        raise ValueError("swap_invalid")
    return values


def _parse_loadavg(text: str) -> tuple[float, float, float]:
    parts = text.strip().split()
    if len(parts) < 3:
        raise ValueError("loadavg_invalid")
    try:
        values = tuple(float(parts[index]) for index in range(3))
    except ValueError as exc:
        raise ValueError("loadavg_invalid") from exc
    if any(value < 0 for value in values):
        raise ValueError("loadavg_invalid")
    return values


def _parse_cpu_count(stat_text: str) -> int:
    count = sum(1 for line in stat_text.splitlines() if _CPU_LINE_RE.match(line))
    if count < 1:
        raise ValueError("cpu_count_missing")
    return count


def _parse_psi(text: str, *, require_full: bool) -> dict:
    rows: dict[str, dict[str, float | int]] = {}
    for raw in text.splitlines():
        parts = raw.strip().split()
        if not parts:
            continue
        category = parts[0]
        if category not in {"some", "full"}:
            continue
        row: dict[str, float | int] = {}
        for part in parts[1:]:
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            try:
                row[key] = int(value) if key == "total" else float(value)
            except ValueError as exc:
                raise ValueError(f"psi_invalid:{category}:{key}") from exc
        for required in ("avg10", "avg60", "avg300", "total"):
            if required not in row:
                raise ValueError(f"psi_missing:{category}:{required}")
        rows[category] = row
    if "some" not in rows:
        raise ValueError("psi_missing:some")
    if require_full and "full" not in rows:
        raise ValueError("psi_missing:full")
    return rows


def _psi_avg10(rows: dict, category: str) -> float:
    row = rows.get(category)
    if not isinstance(row, dict):
        return 0.0
    return float(row.get("avg10") or 0.0)


def classify(metrics: dict) -> dict:
    memory = metrics["memory"]
    load = metrics["load"]
    psi = metrics["psi"]

    reasons: list[str] = []
    memory_reasons: list[str] = []
    io_reasons: list[str] = []
    compute_reasons: list[str] = []

    if float(memory["available_pct"]) <= MEM_AVAILABLE_LOW_PCT:
        memory_reasons.append("memory_available_low")
    if float(psi["memory"]["some_avg10"]) >= MEM_PSI_SOME_PRESSURE:
        memory_reasons.append("memory_psi_some")
    if float(psi["memory"]["full_avg10"]) >= MEM_PSI_FULL_PRESSURE:
        memory_reasons.append("memory_psi_full")
    swap_free_pct = memory.get("swap_free_pct")
    if (
        swap_free_pct is not None
        and float(swap_free_pct) <= SWAP_FREE_LOW_PCT
        and (
            float(memory["available_pct"]) <= MEM_AVAILABLE_LOW_PCT * 2
            or memory_reasons
        )
    ):
        memory_reasons.append("swap_free_low_with_memory_pressure")

    if float(psi["io"]["some_avg10"]) >= IO_PSI_SOME_PRESSURE:
        io_reasons.append("io_psi_some")
    if float(psi["io"]["full_avg10"]) >= IO_PSI_FULL_PRESSURE:
        io_reasons.append("io_psi_full")

    if float(load["load1_per_core"]) >= LOAD_PER_CORE_BUSY:
        compute_reasons.append("load_per_core_busy")
    if float(psi["cpu"]["some_avg10"]) >= CPU_PSI_SOME_BUSY:
        compute_reasons.append("cpu_psi_some")

    reasons.extend(memory_reasons)
    reasons.extend(io_reasons)
    reasons.extend(compute_reasons)

    if memory_reasons and io_reasons:
        state = "mixed_pressure"
        requires_backpressure = True
    elif memory_reasons:
        state = "memory_pressure"
        requires_backpressure = True
    elif io_reasons:
        state = "io_pressure"
        requires_backpressure = True
    elif compute_reasons:
        state = "compute_busy"
        requires_backpressure = False
    else:
        state = "healthy"
        requires_backpressure = False

    return {
        "state": state,
        "reason_codes": reasons,
        "memory_pressure": bool(memory_reasons),
        "io_pressure": bool(io_reasons),
        "compute_busy": bool(compute_reasons),
        "requires_backpressure": requires_backpressure,
        "interpretation": (
            "CPU/load saturation alone is informational; backpressure is recommended only "
            "when memory or IO pressure signals cross the documented thresholds."
        ),
    }


def report(proc_root: Path = Path("/proc"), *, now: datetime | None = None) -> dict:
    proc_root = Path(proc_root)
    if proc_root.is_symlink():
        raise ValueError("proc_root_symlink_not_allowed")
    pressure_root = proc_root / "pressure"

    mem = _parse_meminfo(_read_required(proc_root / "meminfo"))
    load1, load5, load15 = _parse_loadavg(_read_required(proc_root / "loadavg"))
    cpu_count = _parse_cpu_count(_read_required(proc_root / "stat"))
    cpu_psi = _parse_psi(_read_required(pressure_root / "cpu"), require_full=False)
    memory_psi = _parse_psi(_read_required(pressure_root / "memory"), require_full=True)
    io_psi = _parse_psi(_read_required(pressure_root / "io"), require_full=True)

    memory_available_pct = _ratio_pct(mem["MemAvailable"], mem["MemTotal"])
    if memory_available_pct is None:
        raise ValueError("memory_ratio_invalid")
    swap_free_pct = _ratio_pct(mem["SwapFree"], mem["SwapTotal"])

    metrics = {
        "cpu_count": cpu_count,
        "load": {
            "load1": load1,
            "load5": load5,
            "load15": load15,
            "load1_per_core": round(load1 / cpu_count, 3),
        },
        "memory": {
            "total_kib": mem["MemTotal"],
            "available_kib": mem["MemAvailable"],
            "available_pct": memory_available_pct,
            "swap_total_kib": mem["SwapTotal"],
            "swap_free_kib": mem["SwapFree"],
            "swap_free_pct": swap_free_pct,
        },
        "psi": {
            "cpu": {
                "some_avg10": _psi_avg10(cpu_psi, "some"),
            },
            "memory": {
                "some_avg10": _psi_avg10(memory_psi, "some"),
                "full_avg10": _psi_avg10(memory_psi, "full"),
            },
            "io": {
                "some_avg10": _psi_avg10(io_psi, "some"),
                "full_avg10": _psi_avg10(io_psi, "full"),
            },
        },
    }
    assessment = classify(metrics)
    generated_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    return {
        "generated_at": generated_at,
        "state_contract": list(STATE_CONTRACT),
        "assessment": assessment,
        "metrics": metrics,
        "thresholds": {
            "load1_per_core_busy_gte": LOAD_PER_CORE_BUSY,
            "cpu_psi_some_avg10_busy_gte": CPU_PSI_SOME_BUSY,
            "memory_available_pct_low_lte": MEM_AVAILABLE_LOW_PCT,
            "memory_psi_some_avg10_pressure_gte": MEM_PSI_SOME_PRESSURE,
            "memory_psi_full_avg10_pressure_gte": MEM_PSI_FULL_PRESSURE,
            "io_psi_some_avg10_pressure_gte": IO_PSI_SOME_PRESSURE,
            "io_psi_full_avg10_pressure_gte": IO_PSI_FULL_PRESSURE,
            "swap_free_pct_low_lte": SWAP_FREE_LOW_PCT,
        },
        "privacy": "aggregate /proc counters only; no process argv, environment or per-process metadata",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Classify zCloud host CPU/load/memory/IO pressure read-only"
    )
    parser.add_argument("--proc-root", type=Path, default=Path("/proc"))
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)
    payload = report(args.proc_root)
    print(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2 if args.pretty else None,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

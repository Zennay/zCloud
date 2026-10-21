#!/usr/bin/env python3
"""Read-only audit for zCloud control-plane CPU scheduling protection."""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass
from typing import Callable, Iterable

CONTROL_MIN_CPU_WEIGHT = 1000

PROPERTIES = (
    "ActiveState",
    "CPUWeight",
    "CPUQuotaPerSecUSec",
    "MemoryMin",
    "MemoryHigh",
    "MemoryMax",
    "Nice",
    "Slice",
)


@dataclass(frozen=True)
class UnitSpec:
    name: str
    scope: str
    role: str


UNIT_SPECS = (
    UnitSpec("zennay-cloud.service", "system", "control"),
    UnitSpec("chatgpt-firefox.service", "user", "control"),
    UnitSpec("ftmo-autonomous-marathon.service", "system", "compute"),
    UnitSpec("haxlab-autonomy.service", "system", "compute"),
)


def _parse_int(value: str | None) -> int | None:
    raw = (value or "").strip()
    if not raw or raw.lower() in {"infinity", "[not set]"}:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def parse_show(text: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in PROPERTIES:
            parsed[key] = value.strip()
    return parsed


def read_unit(
    spec: UnitSpec,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict:
    command = ["systemctl"]
    if spec.scope == "user":
        command.append("--user")
    command.extend(
        [
            "show",
            spec.name,
            "--no-pager",
            "--property=" + ",".join(PROPERTIES),
        ]
    )
    try:
        completed = runner(
            command,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "name": spec.name,
            "scope": spec.scope,
            "role": spec.role,
            "available": False,
            "active": False,
            "error": type(exc).__name__,
        }

    if completed.returncode != 0:
        return {
            "name": spec.name,
            "scope": spec.scope,
            "role": spec.role,
            "available": False,
            "active": False,
            "error": "systemctl_show_failed",
        }

    props = parse_show(completed.stdout)
    return {
        "name": spec.name,
        "scope": spec.scope,
        "role": spec.role,
        "available": bool(props),
        "active": props.get("ActiveState") == "active",
        "cpu_weight": _parse_int(props.get("CPUWeight")),
        "cpu_quota_usec": _parse_int(props.get("CPUQuotaPerSecUSec")),
        "memory_min": _parse_int(props.get("MemoryMin")),
        "memory_high": _parse_int(props.get("MemoryHigh")),
        "memory_max": _parse_int(props.get("MemoryMax")),
        "nice": _parse_int(props.get("Nice")),
        "slice": props.get("Slice") or None,
    }


def classify(units: Iterable[dict]) -> dict:
    snapshot = list(units)
    controls = [unit for unit in snapshot if unit.get("role") == "control"]
    computes = [unit for unit in snapshot if unit.get("role") == "compute"]
    issues: list[str] = []

    if not controls:
        issues.append("control_units_missing")

    for unit in controls:
        name = str(unit.get("name") or "unknown")
        if not unit.get("available"):
            issues.append(f"{name}:resource_state_unavailable")
            continue
        if not unit.get("active"):
            issues.append(f"{name}:inactive")
        weight = unit.get("cpu_weight")
        if weight is None:
            issues.append(f"{name}:cpu_weight_unknown")
        elif int(weight) < CONTROL_MIN_CPU_WEIGHT:
            issues.append(f"{name}:cpu_weight_below_floor")
        nice = unit.get("nice")
        if nice is None:
            issues.append(f"{name}:nice_unknown")
        elif int(nice) > 0:
            issues.append(f"{name}:positive_nice")

    # CPUWeight is only comparable inside the same systemd manager/scope.
    for control in controls:
        if not control.get("available") or not control.get("active"):
            continue
        control_weight = control.get("cpu_weight")
        if control_weight is None:
            continue
        for compute in computes:
            if (
                compute.get("scope") != control.get("scope")
                or not compute.get("available")
                or not compute.get("active")
            ):
                continue
            compute_weight = compute.get("cpu_weight")
            if compute_weight is None:
                continue
            if int(compute_weight) >= int(control_weight):
                issues.append(
                    f"{control['name']}:active_compute_weight_not_lower:{compute['name']}"
                )

    return {
        "status": "protected" if not issues else "needs_hardening",
        "policy": {
            "control_min_cpu_weight": CONTROL_MIN_CPU_WEIGHT,
            "cross_scope_weight_comparison": False,
        },
        "issues": sorted(set(issues)),
        "control": controls,
        "compute": computes,
    }


def audit(
    *,
    specs: Iterable[UnitSpec] = UNIT_SPECS,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict:
    return classify(read_unit(spec, runner=runner) for spec in specs)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit effective zCloud/Firefox scheduling protection without mutation"
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--require-protected",
        action="store_true",
        help="exit non-zero when the current protection contract is not satisfied",
    )
    args = parser.parse_args(argv)

    result = audit()
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(
            "ZCLOUD_CONTROL_PLANE_RESOURCE_PROTECTION",
            f"status={result['status']}",
            f"issues={len(result['issues'])}",
        )
        for issue in result["issues"]:
            print(" -", issue)

    if args.require_protected and result["status"] != "protected":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

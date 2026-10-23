#!/usr/bin/env python3
"""Read-only bounded diagnostic for the dedicated FTMO research Actions runner."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

POLICY = "zcloud-ftmo-research-runner-diagnostic-v2"
MAX_UNITS = 32
UNIT_RE = re.compile(r"^actions\.runner\.[A-Za-z0-9_.@-]+\.service$")


def _run(args: list[str]) -> str:
    proc = subprocess.run(
        args,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return proc.stdout


def parse_runner_units(text: str) -> list[str]:
    units: list[str] = []
    for raw in text.splitlines():
        token = raw.strip().split(maxsplit=1)[0] if raw.strip() else ""
        if not token:
            continue
        if not UNIT_RE.fullmatch(token):
            continue
        units.append(token)
        if len(units) > MAX_UNITS:
            raise RuntimeError("runner service inventory exceeds bounded limit")
    return sorted(set(units))


def parse_show(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in text.splitlines():
        if "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        if key in {
            "ActiveState",
            "SubState",
            "MainPID",
            "ControlGroup",
            "ExecMainStatus",
            "NRestarts",
        }:
            values[key] = value
    return values


def _count_cgroup_processes(control_group: str) -> tuple[int, int]:
    if not control_group.startswith("/"):
        return 0, 0
    listeners = 0
    workers = 0
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            cgroup = (proc / "cgroup").read_text(encoding="utf-8", errors="replace")
            if control_group not in cgroup:
                continue
            comm = (proc / "comm").read_text(encoding="utf-8", errors="replace").strip()
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        if comm == "Runner.Listener":
            listeners += 1
        elif comm == "Runner.Worker":
            workers += 1
    return listeners, workers


def classify_unit(
    unit: str,
    show: dict[str, str],
    listeners: int,
    workers: int,
) -> dict[str, object]:
    active = show.get("ActiveState", "unknown")
    sub = show.get("SubState", "unknown")
    if active != "active" and (listeners > 0 or workers > 0):
        status = "orphaned_processes"
        recovery_advice = "reconcile_orphans_before_service_start"
    elif active != "active":
        status = "offline"
        recovery_advice = "service_start_candidate"
    elif listeners != 1:
        status = "degraded"
        recovery_advice = "inspect_listener_state"
    elif workers > 0:
        status = "busy"
        recovery_advice = "leave_inflight_work_untouched"
    else:
        status = "idle"
        recovery_advice = "runner_ready"
    return {
        "unit": unit,
        "active_state": active,
        "sub_state": sub,
        "exec_main_status": show.get("ExecMainStatus", ""),
        "restart_count": int(show.get("NRestarts", "0") or 0),
        "listener_count": listeners,
        "worker_count": workers,
        "status": status,
        "recovery_advice": recovery_advice,
    }


def collect() -> dict[str, object]:
    units = parse_runner_units(
        _run(
            [
                "systemctl",
                "list-units",
                "--all",
                "--type=service",
                "--plain",
                "--no-legend",
                "actions.runner.*.service",
            ]
        )
    )
    candidates: list[dict[str, object]] = []
    for unit in units:
        lowered = unit.lower()
        if "ftmo" not in lowered and "research" not in lowered:
            continue
        show = parse_show(
            _run(
                [
                    "systemctl",
                    "show",
                    unit,
                    "--property=ActiveState",
                    "--property=SubState",
                    "--property=MainPID",
                    "--property=ControlGroup",
                    "--property=ExecMainStatus",
                    "--property=NRestarts",
                ]
            )
        )
        listeners, workers = _count_cgroup_processes(show.get("ControlGroup", ""))
        candidates.append(classify_unit(unit, show, listeners, workers))

    if not candidates:
        overall = "missing"
    elif len(candidates) > 1:
        overall = "ambiguous"
    else:
        overall = str(candidates[0]["status"])

    return {
        "policy": POLICY,
        "mutation_performed": False,
        "runner_service_count": len(units),
        "candidate_count": len(candidates),
        "status": overall,
        "candidates": candidates,
    }


def main() -> int:
    payload = collect()
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

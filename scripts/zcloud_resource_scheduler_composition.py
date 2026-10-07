#!/usr/bin/env python3
"""Compose bounded zCloud resource-scheduler evidence into a mutation-free plan.

This module deliberately performs no SQLite, systemd, browser, GitHub, or service
writes. It is an offline composition contract for the later serialized runtime
scheduler integration.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
MAX_INPUT_BYTES = 256 * 1024
MAX_PROJECTS = 256
MAX_CPU_CORES = 64.0
MAX_PARALLEL_JOBS = 64
QUEUE_PRESSURE_STATES = {"healthy", "busy", "stalled"}


class CompositionError(ValueError):
    """Raised when scheduler evidence is malformed or internally incoherent."""


def _exact_keys(value: dict[str, Any], expected: set[str], field: str) -> None:
    if set(value) != expected:
        raise CompositionError(f"{field}_schema_invalid")


def _number(value: Any, field: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CompositionError(f"{field}_invalid")
    number = float(value)
    if not math.isfinite(number):
        raise CompositionError(f"{field}_invalid")
    if positive:
        if number <= 0 or number > MAX_CPU_CORES:
            raise CompositionError(f"{field}_invalid")
    elif number < 0 or number > MAX_CPU_CORES:
        raise CompositionError(f"{field}_invalid")
    return number


def _bounded_int(value: Any, field: str, *, minimum: int = 0, maximum: int = MAX_PARALLEL_JOBS) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CompositionError(f"{field}_invalid")
    if value < minimum or value > maximum:
        raise CompositionError(f"{field}_invalid")
    return value


def _read_json(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise CompositionError("symlink_input_forbidden")
    if not path.is_file():
        raise CompositionError("input_not_regular_file")
    raw = path.read_bytes()
    if len(raw) > MAX_INPUT_BYTES:
        raise CompositionError("input_too_large")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompositionError("input_invalid_json") from exc
    if not isinstance(value, dict):
        raise CompositionError("input_not_object")
    return value


def _validate(payload: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    _exact_keys(payload, {"schema_version", "host", "projects"}, "root")
    if payload["schema_version"] != SCHEMA_VERSION or isinstance(payload["schema_version"], bool):
        raise CompositionError("schema_version_invalid")

    host = payload["host"]
    if not isinstance(host, dict):
        raise CompositionError("host_invalid")
    _exact_keys(
        host,
        {
            "capacity_cpu_cores",
            "protected_reserve_cpu_cores",
            "idle_borrowable_cpu_cores",
            "backpressure_required",
            "queue_pressure_state",
            "source_complete",
        },
        "host",
    )
    capacity = _number(host["capacity_cpu_cores"], "capacity_cpu_cores", positive=True)
    protected_reserve = _number(host["protected_reserve_cpu_cores"], "protected_reserve_cpu_cores")
    idle_borrowable = _number(host["idle_borrowable_cpu_cores"], "idle_borrowable_cpu_cores")
    if protected_reserve > capacity:
        raise CompositionError("protected_reserve_exceeds_capacity")
    if idle_borrowable > capacity:
        raise CompositionError("idle_borrowable_exceeds_capacity")
    if type(host["backpressure_required"]) is not bool:
        raise CompositionError("backpressure_required_invalid")
    if type(host["source_complete"]) is not bool:
        raise CompositionError("source_complete_invalid")
    pressure = host["queue_pressure_state"]
    if pressure not in QUEUE_PRESSURE_STATES:
        raise CompositionError("queue_pressure_state_invalid")

    projects = payload["projects"]
    if not isinstance(projects, list) or not projects or len(projects) > MAX_PROJECTS:
        raise CompositionError("projects_invalid")

    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_ranks: set[int] = set()
    protected_minimum = 0.0

    expected_project_keys = {
        "project_id",
        "scheduler_rank",
        "runnable",
        "safety_admitted",
        "allow_idle_capacity_borrow",
        "protected",
        "minimum_cpu_cores",
        "target_cpu_cores",
        "maximum_cpu_cores",
        "requested_cpu_cores",
        "safe_parallel_jobs",
        "current_parallel_jobs",
        "project_parallel_cap",
        "cpu_per_job_cores",
    }

    for row in projects:
        if not isinstance(row, dict):
            raise CompositionError("project_not_object")
        _exact_keys(row, expected_project_keys, "project")
        project_id = row["project_id"]
        if (
            not isinstance(project_id, str)
            or not project_id
            or project_id != project_id.strip()
            or len(project_id) > 64
        ):
            raise CompositionError("project_id_invalid")
        if project_id in seen_ids:
            raise CompositionError("duplicate_project_id")
        seen_ids.add(project_id)

        rank = _bounded_int(row["scheduler_rank"], "scheduler_rank", maximum=MAX_PROJECTS - 1)
        if rank in seen_ranks:
            raise CompositionError("duplicate_scheduler_rank")
        seen_ranks.add(rank)

        for field in ("runnable", "safety_admitted", "allow_idle_capacity_borrow", "protected"):
            if type(row[field]) is not bool:
                raise CompositionError(f"{field}_invalid")

        minimum = _number(row["minimum_cpu_cores"], f"{project_id}.minimum_cpu_cores")
        target = _number(row["target_cpu_cores"], f"{project_id}.target_cpu_cores")
        maximum = _number(row["maximum_cpu_cores"], f"{project_id}.maximum_cpu_cores")
        requested = _number(row["requested_cpu_cores"], f"{project_id}.requested_cpu_cores")
        cpu_per_job = _number(row["cpu_per_job_cores"], f"{project_id}.cpu_per_job_cores", positive=True)
        if not minimum <= target <= maximum:
            raise CompositionError(f"{project_id}.profile_order_invalid")
        if requested > maximum:
            raise CompositionError(f"{project_id}.requested_exceeds_maximum")

        safe_jobs = _bounded_int(row["safe_parallel_jobs"], f"{project_id}.safe_parallel_jobs")
        current_jobs = _bounded_int(row["current_parallel_jobs"], f"{project_id}.current_parallel_jobs")
        cap = _bounded_int(
            row["project_parallel_cap"],
            f"{project_id}.project_parallel_cap",
            minimum=1,
        )
        if current_jobs > cap:
            raise CompositionError(f"{project_id}.current_parallel_exceeds_cap")
        if row["protected"]:
            protected_minimum += minimum

        normalized.append(
            {
                "project_id": project_id,
                "scheduler_rank": rank,
                "runnable": row["runnable"],
                "safety_admitted": row["safety_admitted"],
                "allow_idle_capacity_borrow": row["allow_idle_capacity_borrow"],
                "protected": row["protected"],
                "minimum_cpu_cores": minimum,
                "target_cpu_cores": target,
                "maximum_cpu_cores": maximum,
                "requested_cpu_cores": requested,
                "safe_parallel_jobs": safe_jobs,
                "current_parallel_jobs": current_jobs,
                "project_parallel_cap": cap,
                "cpu_per_job_cores": cpu_per_job,
            }
        )

    if protected_minimum > protected_reserve + 1e-9:
        raise CompositionError("protected_minimum_exceeds_reserved_capacity")

    host_norm = {
        "capacity_cpu_cores": capacity,
        "protected_reserve_cpu_cores": protected_reserve,
        "idle_borrowable_cpu_cores": idle_borrowable,
        "backpressure_required": host["backpressure_required"],
        "queue_pressure_state": pressure,
        "source_complete": host["source_complete"],
    }
    return host_norm, sorted(normalized, key=lambda row: row["scheduler_rank"])


def compose_scheduler_plan(payload: dict[str, Any]) -> dict[str, Any]:
    host, projects = _validate(payload)
    capacity = host["capacity_cpu_cores"]

    minimum_total = sum(row["minimum_cpu_cores"] for row in projects)
    if minimum_total > capacity + 1e-9:
        return {
            "schema_version": SCHEMA_VERSION,
            "contract": "resource-scheduler-composition-v1",
            "status": "blocked",
            "decision_ready": False,
            "reason": "minimum_capacity_exceeded",
            "base_allocations": [],
            "borrow_admissions": [],
            "totals": {
                "minimum_cpu_cores": minimum_total,
                "planned_cpu_cores": 0.0,
                "borrowed_cpu_cores": 0.0,
                "admitted_parallel_jobs": 0,
            },
            "runtime_mutation": False,
        }

    allocations = {
        row["project_id"]: row["minimum_cpu_cores"]
        for row in projects
    }
    remaining = capacity - minimum_total

    # First satisfy bounded normal demand up to each project's target in scheduler order.
    for row in projects:
        if not (row["runnable"] and row["safety_admitted"]):
            continue
        desired = max(row["minimum_cpu_cores"], min(row["requested_cpu_cores"], row["target_cpu_cores"]))
        need = max(0.0, desired - allocations[row["project_id"]])
        grant = min(need, remaining)
        allocations[row["project_id"]] += grant
        remaining -= grant

    base_allocations = [
        {
            "project_id": row["project_id"],
            "scheduler_rank": row["scheduler_rank"],
            "cpu_cores": round(allocations[row["project_id"]], 6),
            "target_satisfied": allocations[row["project_id"]] + 1e-9
            >= max(row["minimum_cpu_cores"], min(row["requested_cpu_cores"], row["target_cpu_cores"])),
        }
        for row in projects
    ]

    blocked_reason = None
    if not host["source_complete"]:
        blocked_reason = "source_incomplete"
    elif host["backpressure_required"]:
        blocked_reason = "backpressure_required"
    elif host["queue_pressure_state"] != "healthy":
        blocked_reason = f"queue_pressure_{host['queue_pressure_state']}"

    borrow_admissions: list[dict[str, Any]] = []
    borrowed = 0.0
    admitted_jobs = 0

    if blocked_reason is None:
        borrow_budget = min(host["idle_borrowable_cpu_cores"], remaining)
        for row in projects:
            if borrow_budget <= 1e-9:
                break
            if not (
                row["runnable"]
                and row["safety_admitted"]
                and row["allow_idle_capacity_borrow"]
            ):
                continue

            current_alloc = allocations[row["project_id"]]
            cpu_room = max(0.0, row["maximum_cpu_cores"] - current_alloc)
            job_room = max(0, row["project_parallel_cap"] - row["current_parallel_jobs"])
            safe_jobs = min(row["safe_parallel_jobs"], job_room)
            if safe_jobs <= 0 or cpu_room + 1e-9 < row["cpu_per_job_cores"]:
                continue

            fit_by_project = int((cpu_room + 1e-9) // row["cpu_per_job_cores"])
            fit_by_host = int((borrow_budget + 1e-9) // row["cpu_per_job_cores"])
            grant_jobs = min(safe_jobs, fit_by_project, fit_by_host)
            if grant_jobs <= 0:
                continue

            grant_cpu = grant_jobs * row["cpu_per_job_cores"]
            allocations[row["project_id"]] += grant_cpu
            borrow_budget -= grant_cpu
            remaining -= grant_cpu
            borrowed += grant_cpu
            admitted_jobs += grant_jobs
            borrow_admissions.append(
                {
                    "project_id": row["project_id"],
                    "scheduler_rank": row["scheduler_rank"],
                    "additional_jobs": grant_jobs,
                    "additional_cpu_cores": round(grant_cpu, 6),
                }
            )

    planned_total = sum(allocations.values())
    if planned_total > capacity + 1e-8:
        raise CompositionError("planned_capacity_exceeded")

    if blocked_reason == "source_incomplete":
        status = "incomplete"
        decision_ready = False
    elif blocked_reason is not None:
        # Pressure is a valid scheduler decision: preserve the bounded base plan
        # while holding opportunistic borrowing/parallel expansion.
        status = "held"
        decision_ready = True
    else:
        status = "ready"
        decision_ready = True

    return {
        "schema_version": SCHEMA_VERSION,
        "contract": "resource-scheduler-composition-v1",
        "status": status,
        "decision_ready": decision_ready,
        "reason": blocked_reason,
        "base_allocations": base_allocations,
        "borrow_admissions": borrow_admissions,
        "totals": {
            "minimum_cpu_cores": round(minimum_total, 6),
            "planned_cpu_cores": round(planned_total, 6),
            "borrowed_cpu_cores": round(borrowed, 6),
            "admitted_parallel_jobs": admitted_jobs,
        },
        "guardrails": {
            "source_complete": host["source_complete"],
            "backpressure_required": host["backpressure_required"],
            "queue_pressure_state": host["queue_pressure_state"],
            "protected_reserve_preserved": True,
            "capacity_exceeded": False,
            "mutation_performed": False,
        },
        "runtime_mutation": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--require-ready", action="store_true")
    args = parser.parse_args()

    try:
        payload = _read_json(Path(args.input))
        result = compose_scheduler_plan(payload)
    except CompositionError as exc:
        result = {
            "schema_version": SCHEMA_VERSION,
            "contract": "resource-scheduler-composition-v1",
            "status": "invalid",
            "decision_ready": False,
            "error": str(exc),
            "runtime_mutation": False,
        }
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 2

    if args.json:
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    else:
        print(json.dumps(result, indent=2, sort_keys=True))

    if args.require_ready and not result["decision_ready"]:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

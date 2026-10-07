#!/usr/bin/env python3
"""Side-effect-free admission policy for borrowing only genuinely idle CPU capacity."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

POLICY_SCHEMA_VERSION = 1
EVIDENCE_SCHEMA_VERSION = 1
MAX_EVIDENCE_BYTES = 256 * 1024
MAX_PROJECTS = 256
PRESSURE_STATES = {
    "healthy",
    "compute_busy",
    "memory_pressure",
    "io_pressure",
    "mixed_pressure",
}
BLOCKING_PRESSURE_STATES = {"memory_pressure", "io_pressure", "mixed_pressure"}


class BorrowAdmissionError(ValueError):
    """Raised when idle-capacity evidence is ambiguous or unsafe to use."""


def _read_json(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise BorrowAdmissionError("symlink_input_forbidden")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise BorrowAdmissionError("input_unreadable") from exc
    if len(raw) > MAX_EVIDENCE_BYTES:
        raise BorrowAdmissionError("input_too_large")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BorrowAdmissionError("input_invalid_json") from exc
    if not isinstance(payload, dict):
        raise BorrowAdmissionError("input_not_object")
    return payload


def _number(value: Any, *, error: str, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BorrowAdmissionError(error)
    number = float(value)
    if not math.isfinite(number):
        raise BorrowAdmissionError(error)
    if positive and number <= 0:
        raise BorrowAdmissionError(error)
    if not positive and number < 0:
        raise BorrowAdmissionError(error)
    return number


def _exact_fields(payload: dict[str, Any], allowed: set[str], error: str) -> None:
    if set(payload) != allowed:
        raise BorrowAdmissionError(error)


def admit_idle_capacity_borrow(evidence: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(evidence, dict):
        raise BorrowAdmissionError("evidence_not_object")
    if set(evidence) - {"schema_version", "host", "projects"}:
        raise BorrowAdmissionError("evidence_unknown_top_level_field")

    schema_version = evidence.get("schema_version")
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version != EVIDENCE_SCHEMA_VERSION
    ):
        raise BorrowAdmissionError("evidence_schema_invalid")

    host = evidence.get("host")
    if not isinstance(host, dict):
        raise BorrowAdmissionError("host_missing")
    _exact_fields(
        host,
        {
            "capacity_cpu_cores",
            "protected_reserve_cpu_cores",
            "admitted_non_protected_cpu_cores",
            "pressure_state",
            "backpressure_required",
        },
        "host_fields_invalid",
    )

    capacity = _number(
        host["capacity_cpu_cores"], error="capacity_cpu_cores_invalid", positive=True
    )
    reserve = _number(
        host["protected_reserve_cpu_cores"],
        error="protected_reserve_cpu_cores_invalid",
    )
    admitted = _number(
        host["admitted_non_protected_cpu_cores"],
        error="admitted_non_protected_cpu_cores_invalid",
    )
    if reserve > capacity:
        raise BorrowAdmissionError("protected_reserve_exceeds_capacity")
    non_protected_ceiling = capacity - reserve
    if admitted > non_protected_ceiling:
        raise BorrowAdmissionError("admitted_non_protected_exceeds_ceiling")

    pressure_state = host["pressure_state"]
    if not isinstance(pressure_state, str) or pressure_state not in PRESSURE_STATES:
        raise BorrowAdmissionError("pressure_state_invalid")
    backpressure_required = host["backpressure_required"]
    if not isinstance(backpressure_required, bool):
        raise BorrowAdmissionError("backpressure_required_invalid")

    rows = evidence.get("projects")
    if not isinstance(rows, list):
        raise BorrowAdmissionError("projects_missing")
    if len(rows) > MAX_PROJECTS:
        raise BorrowAdmissionError("project_limit")

    seen_projects: set[str] = set()
    seen_ranks: set[int] = set()
    projects: list[dict[str, Any]] = []

    for row in rows:
        if not isinstance(row, dict):
            raise BorrowAdmissionError("project_row_invalid")
        _exact_fields(
            row,
            {
                "project_id",
                "scheduler_rank",
                "runnable",
                "safety_admitted",
                "allow_idle_capacity_borrow",
                "protected",
                "requested_extra_cpu_cores",
            },
            "project_fields_invalid",
        )

        project_id = row["project_id"]
        if (
            not isinstance(project_id, str)
            or not project_id
            or project_id != project_id.strip()
        ):
            raise BorrowAdmissionError("project_id_invalid")
        if project_id in seen_projects:
            raise BorrowAdmissionError("project_duplicate")
        seen_projects.add(project_id)

        rank = row["scheduler_rank"]
        if isinstance(rank, bool) or not isinstance(rank, int) or rank < 0:
            raise BorrowAdmissionError("scheduler_rank_invalid")
        if rank in seen_ranks:
            raise BorrowAdmissionError("scheduler_rank_duplicate")
        seen_ranks.add(rank)

        for field in (
            "runnable",
            "safety_admitted",
            "allow_idle_capacity_borrow",
            "protected",
        ):
            if not isinstance(row[field], bool):
                raise BorrowAdmissionError(f"{field}_invalid")

        requested = _number(
            row["requested_extra_cpu_cores"],
            error="requested_extra_cpu_cores_invalid",
        )
        projects.append(
            {
                "project_id": project_id,
                "scheduler_rank": rank,
                "runnable": row["runnable"],
                "safety_admitted": row["safety_admitted"],
                "allow_idle_capacity_borrow": row["allow_idle_capacity_borrow"],
                "protected": row["protected"],
                "requested_extra_cpu_cores": requested,
            }
        )

    projects.sort(key=lambda row: (row["scheduler_rank"], row["project_id"]))
    available = round(max(0.0, non_protected_ceiling - admitted), 6)
    initial_available = available
    pressure_blocks = (
        backpressure_required or pressure_state in BLOCKING_PRESSURE_STATES
    )

    admitted_rows: list[dict[str, Any]] = []
    denied_rows: list[dict[str, Any]] = []

    for row in projects:
        project_id = row["project_id"]
        requested = row["requested_extra_cpu_cores"]

        reason = None
        if not row["runnable"]:
            reason = "not_runnable"
        elif not row["safety_admitted"]:
            reason = "safety_not_admitted"
        elif not row["allow_idle_capacity_borrow"]:
            reason = "borrowing_not_allowed"
        elif row["protected"]:
            reason = "protected_project_not_borrower"
        elif requested <= 0:
            reason = "no_extra_capacity_requested"
        elif pressure_blocks:
            reason = "backpressure_active"
        elif available <= 0:
            reason = "idle_capacity_exhausted"

        if reason is not None:
            denied_rows.append(
                {
                    "project_id": project_id,
                    "scheduler_rank": row["scheduler_rank"],
                    "reason": reason,
                    "requested_extra_cpu_cores": requested,
                    "borrow_cpu_cores": 0.0,
                }
            )
            continue

        grant = round(min(requested, available), 6)
        available = round(max(0.0, available - grant), 6)
        admitted_rows.append(
            {
                "project_id": project_id,
                "scheduler_rank": row["scheduler_rank"],
                "requested_extra_cpu_cores": requested,
                "borrow_cpu_cores": grant,
                "fully_satisfied": grant >= requested,
            }
        )

    borrowed = round(sum(row["borrow_cpu_cores"] for row in admitted_rows), 6)
    result = {
        "schema_version": POLICY_SCHEMA_VERSION,
        "policy": "idle-capacity-borrow-admission-v1",
        "host": {
            "capacity_cpu_cores": capacity,
            "protected_reserve_cpu_cores": reserve,
            "admitted_non_protected_cpu_cores": admitted,
            "idle_borrowable_cpu_cores": initial_available,
            "remaining_idle_cpu_cores": available,
            "pressure_state": pressure_state,
            "backpressure_required": backpressure_required,
        },
        "admitted": admitted_rows,
        "denied": denied_rows,
        "totals": {
            "borrowed_cpu_cores": borrowed,
            "admitted_projects": len(admitted_rows),
            "denied_projects": len(denied_rows),
        },
        "guardrails": {
            "protected_reserve_spendable_by_borrowers": False,
            "compute_busy_alone_blocks_borrowing": False,
            "memory_or_io_pressure_blocks_borrowing": True,
            "borrowing_may_create_runnable_work": False,
            "borrowing_may_bypass_safety_admission": False,
            "borrowing_may_preempt_existing_allocation": False,
            "scheduler_rank_is_input_not_inferred": True,
            "mutation_performed": False,
        },
    }

    if borrowed > initial_available + 1e-9:
        raise BorrowAdmissionError("borrow_exceeds_idle_capacity")
    if reserve + admitted + borrowed > capacity + 1e-9:
        raise BorrowAdmissionError("borrow_violates_protected_reserve")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Admit bounded CPU borrowing only from genuinely idle non-protected capacity"
    )
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        result = admit_idle_capacity_borrow(_read_json(args.evidence))
    except BorrowAdmissionError as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        else:
            print(f"ZCLOUD_IDLE_BORROW_BLOCKED error={exc}")
        return 2

    if args.json:
        print(json.dumps({"ok": True, "result": result}, sort_keys=True))
    else:
        print(
            "ZCLOUD_IDLE_BORROW_GREEN",
            f"borrowable={result['host']['idle_borrowable_cpu_cores']}",
            f"borrowed={result['totals']['borrowed_cpu_cores']}",
            f"admitted={result['totals']['admitted_projects']}",
            f"denied={result['totals']['denied_projects']}",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

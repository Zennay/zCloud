#!/usr/bin/env python3
"""Side-effect-free admission policy for extra jobs using proven spare CPU."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
MAX_INPUT_BYTES = 256 * 1024
MAX_PROJECTS = 256
MAX_SAFE_JOBS_PER_PROJECT = 64


class ParallelExpansionError(ValueError):
    """Raised when parallel-expansion evidence is ambiguous or unsafe."""


def _read_json(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise ParallelExpansionError("symlink_input_forbidden")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ParallelExpansionError("input_unreadable") from exc
    if len(raw) > MAX_INPUT_BYTES:
        raise ParallelExpansionError("input_too_large")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ParallelExpansionError("input_invalid_json") from exc
    if not isinstance(payload, dict):
        raise ParallelExpansionError("input_not_object")
    return payload


def _finite_nonnegative(value: Any, error: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ParallelExpansionError(error)
    number = float(value)
    if not math.isfinite(number):
        raise ParallelExpansionError(error)
    if positive and number <= 0:
        raise ParallelExpansionError(error)
    if not positive and number < 0:
        raise ParallelExpansionError(error)
    return number


def _strict_int(value: Any, error: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ParallelExpansionError(error)
    return value


def plan_parallel_expansion(evidence: dict[str, Any]) -> dict[str, Any]:
    if set(evidence) != {"schema_version", "host", "projects"}:
        raise ParallelExpansionError("top_level_fields_invalid")
    schema = evidence["schema_version"]
    if isinstance(schema, bool) or not isinstance(schema, int) or schema != SCHEMA_VERSION:
        raise ParallelExpansionError("schema_version_invalid")

    host = evidence["host"]
    if not isinstance(host, dict) or set(host) != {
        "idle_borrowable_cpu_cores",
        "backpressure_required",
    }:
        raise ParallelExpansionError("host_fields_invalid")
    available = _finite_nonnegative(
        host["idle_borrowable_cpu_cores"],
        "idle_borrowable_cpu_cores_invalid",
    )
    initial_available = available
    backpressure = host["backpressure_required"]
    if not isinstance(backpressure, bool):
        raise ParallelExpansionError("backpressure_required_invalid")

    rows = evidence["projects"]
    if not isinstance(rows, list):
        raise ParallelExpansionError("projects_invalid")
    if len(rows) > MAX_PROJECTS:
        raise ParallelExpansionError("project_limit")

    seen_projects: set[str] = set()
    seen_ranks: set[int] = set()
    candidates: list[dict[str, Any]] = []

    for row in rows:
        if not isinstance(row, dict) or set(row) != {
            "project_id",
            "scheduler_rank",
            "runnable",
            "safety_admitted",
            "safe_parallel_jobs",
            "current_parallel_jobs",
            "project_parallel_cap",
            "cpu_per_job_cores",
        }:
            raise ParallelExpansionError("project_fields_invalid")

        project_id = row["project_id"]
        if (
            not isinstance(project_id, str)
            or not project_id
            or project_id != project_id.strip()
        ):
            raise ParallelExpansionError("project_id_invalid")
        if project_id in seen_projects:
            raise ParallelExpansionError("project_duplicate")
        seen_projects.add(project_id)

        rank = _strict_int(row["scheduler_rank"], "scheduler_rank_invalid")
        if rank in seen_ranks:
            raise ParallelExpansionError("scheduler_rank_duplicate")
        seen_ranks.add(rank)

        runnable = row["runnable"]
        safety_admitted = row["safety_admitted"]
        if not isinstance(runnable, bool):
            raise ParallelExpansionError("runnable_invalid")
        if not isinstance(safety_admitted, bool):
            raise ParallelExpansionError("safety_admitted_invalid")

        safe_jobs = _strict_int(row["safe_parallel_jobs"], "safe_parallel_jobs_invalid")
        if safe_jobs > MAX_SAFE_JOBS_PER_PROJECT:
            raise ParallelExpansionError("safe_parallel_jobs_limit")
        current = _strict_int(
            row["current_parallel_jobs"], "current_parallel_jobs_invalid"
        )
        cap = _strict_int(row["project_parallel_cap"], "project_parallel_cap_invalid")
        if current > cap:
            raise ParallelExpansionError("current_parallel_jobs_exceeds_cap")
        cpu_per_job = _finite_nonnegative(
            row["cpu_per_job_cores"],
            "cpu_per_job_cores_invalid",
            positive=True,
        )

        candidates.append(
            {
                "project_id": project_id,
                "scheduler_rank": rank,
                "runnable": runnable,
                "safety_admitted": safety_admitted,
                "safe_parallel_jobs": safe_jobs,
                "current_parallel_jobs": current,
                "project_parallel_cap": cap,
                "cpu_per_job_cores": cpu_per_job,
            }
        )

    candidates.sort(key=lambda row: (row["scheduler_rank"], row["project_id"]))
    admissions: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []

    for row in candidates:
        project_id = row["project_id"]
        reason = None
        if backpressure:
            reason = "backpressure_active"
        elif not row["runnable"]:
            reason = "not_runnable"
        elif not row["safety_admitted"]:
            reason = "safety_not_admitted"
        elif row["safe_parallel_jobs"] == 0:
            reason = "no_safe_parallel_jobs"
        elif row["current_parallel_jobs"] >= row["project_parallel_cap"]:
            reason = "project_parallel_cap_reached"
        elif available + 1e-9 < row["cpu_per_job_cores"]:
            reason = "insufficient_idle_cpu_for_one_job"

        cap_room = row["project_parallel_cap"] - row["current_parallel_jobs"]
        max_jobs_by_cpu = int((available + 1e-9) // row["cpu_per_job_cores"])
        admitted_jobs = 0 if reason else min(
            row["safe_parallel_jobs"],
            cap_room,
            max_jobs_by_cpu,
        )

        if admitted_jobs <= 0:
            if reason is None:
                reason = "insufficient_idle_cpu_for_one_job"
            excluded.append(
                {
                    "project_id": project_id,
                    "scheduler_rank": row["scheduler_rank"],
                    "reason": reason,
                    "admitted_jobs": 0,
                    "admitted_cpu_cores": 0.0,
                }
            )
            continue

        admitted_cpu = round(admitted_jobs * row["cpu_per_job_cores"], 6)
        available = round(max(0.0, available - admitted_cpu), 6)
        admissions.append(
            {
                "project_id": project_id,
                "scheduler_rank": row["scheduler_rank"],
                "admitted_jobs": admitted_jobs,
                "admitted_cpu_cores": admitted_cpu,
                "remaining_project_cap": cap_room - admitted_jobs,
                "safe_jobs_not_admitted": row["safe_parallel_jobs"] - admitted_jobs,
            }
        )

    total_cpu = round(sum(row["admitted_cpu_cores"] for row in admissions), 6)
    if total_cpu > initial_available + 1e-9:
        raise ParallelExpansionError("admission_exceeds_idle_cpu")

    return {
        "schema_version": SCHEMA_VERSION,
        "policy": "safe-parallel-expansion-v1",
        "host": {
            "initial_idle_borrowable_cpu_cores": initial_available,
            "remaining_idle_borrowable_cpu_cores": available,
            "backpressure_required": backpressure,
        },
        "admissions": admissions,
        "excluded": excluded,
        "totals": {
            "admitted_jobs": sum(row["admitted_jobs"] for row in admissions),
            "admitted_cpu_cores": total_cpu,
        },
        "guardrails": {
            "safe_parallel_job_count_is_upstream_evidence": True,
            "scheduler_rank_is_upstream_evidence": True,
            "project_cap_may_be_exceeded": False,
            "idle_cpu_budget_may_be_exceeded": False,
            "backpressure_may_be_bypassed": False,
            "runnable_or_safety_may_be_inferred": False,
            "partial_jobs_allowed": False,
            "mutation_performed": False,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Plan extra parallel jobs within proven spare CPU and safety caps"
    )
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        result = plan_parallel_expansion(_read_json(args.evidence))
    except ParallelExpansionError as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        else:
            print(f"ZCLOUD_SAFE_PARALLEL_BLOCKED error={exc}")
        return 2

    if args.json:
        print(json.dumps({"ok": True, "result": result}, sort_keys=True))
    else:
        print(
            "ZCLOUD_SAFE_PARALLEL_GREEN",
            f"jobs={result['totals']['admitted_jobs']}",
            f"cpu={result['totals']['admitted_cpu_cores']}",
            f"remaining={result['host']['remaining_idle_borrowable_cpu_cores']}",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

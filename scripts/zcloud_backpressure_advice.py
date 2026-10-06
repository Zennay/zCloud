#!/usr/bin/env python3
"""Read-only backpressure advice from worker-scaling and host-pressure evidence.

This module never mutates scheduler/governor state. It only combines two
existing observational signals into a bounded recommendation contract.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

if __package__:
    from scripts import worker_scaling_report as scaling
    from scripts import zcloud_host_pressure_report as pressure
else:
    import worker_scaling_report as scaling
    import zcloud_host_pressure_report as pressure

TOKEN_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
VALID_DECISIONS = (
    "no_backpressure",
    "backpressure_recommended",
    "insufficient_data",
)


def _project_token(value: str) -> str:
    text = str(value or "").strip()
    if not TOKEN_RE.fullmatch(text):
        raise ValueError("invalid_project")
    return text


def decide(scaling_assessment: dict, host_assessment: dict) -> dict:
    scaling_state = str((scaling_assessment or {}).get("state") or "insufficient_data")
    host_state = str((host_assessment or {}).get("state") or "unknown")
    host_requires_backpressure = bool(
        (host_assessment or {}).get("requires_backpressure")
    )

    if host_requires_backpressure:
        return {
            "decision": "backpressure_recommended",
            "apply_backpressure": True,
            "recommended_action": "block_new_capacity",
            "reason_codes": ["host_memory_or_io_pressure"],
        }

    if scaling_state == "diminishing_returns":
        return {
            "decision": "backpressure_recommended",
            "apply_backpressure": True,
            "recommended_action": "reduce_parallelism_candidate",
            "reason_codes": ["worker_diminishing_returns"],
        }

    if scaling_state in {"useful_scaling", "single_worker"}:
        reasons = ["worker_scaling_useful"] if scaling_state == "useful_scaling" else ["single_worker"]
        if host_state == "compute_busy":
            reasons.append("cpu_busy_without_memory_io_pressure")
        return {
            "decision": "no_backpressure",
            "apply_backpressure": False,
            "recommended_action": "none",
            "reason_codes": reasons,
        }

    if scaling_state == "inconclusive":
        return {
            "decision": "insufficient_data",
            "apply_backpressure": False,
            "recommended_action": "collect_more_worker_evidence",
            "reason_codes": ["worker_scaling_inconclusive"],
        }

    return {
        "decision": "insufficient_data",
        "apply_backpressure": False,
        "recommended_action": "collect_more_worker_evidence",
        "reason_codes": ["worker_scaling_insufficient_data"],
    }


def report(
    db: Path,
    *,
    project: str,
    hours: float = 12.0,
    proc_root: Path = Path("/proc"),
    now: datetime | None = None,
) -> dict:
    project_id = _project_token(project)
    generated_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)

    scaling_payload = scaling.report(
        Path(db),
        hours=hours,
        project=project_id,
        now=generated_at,
    )
    project_rows = [
        row
        for row in (scaling_payload.get("projects") or [])
        if str(row.get("project_id") or "") == project_id
    ]
    if len(project_rows) > 1:
        raise ValueError("duplicate_project_scaling_evidence")
    scaling_project = project_rows[0] if project_rows else None
    scaling_assessment = (
        dict(scaling_project.get("assessment") or {})
        if scaling_project
        else {"state": "insufficient_data"}
    )

    pressure_payload = pressure.report(Path(proc_root), now=generated_at)
    host_assessment = dict(pressure_payload.get("assessment") or {})
    decision = decide(scaling_assessment, host_assessment)

    assessment = {
        **decision,
        "project_id": project_id,
        "scaling_state": str(scaling_assessment.get("state") or "insufficient_data"),
        "host_pressure_state": str(host_assessment.get("state") or "unknown"),
    }
    evidence = {
        "desired_workers": (
            int(scaling_project.get("desired_workers"))
            if scaling_project and scaling_project.get("desired_workers") is not None
            else None
        ),
        "completed_generations": (
            int(scaling_project.get("completed_generations"))
            if scaling_project and scaling_project.get("completed_generations") is not None
            else None
        ),
        "extra_vs_primary_throughput_ratio": scaling_assessment.get(
            "extra_vs_primary_throughput_ratio"
        ),
        "extra_idle_blocked_pct": scaling_assessment.get("extra_idle_blocked_pct"),
        "host_compute_busy": bool(host_assessment.get("compute_busy")),
        "host_memory_pressure": bool(host_assessment.get("memory_pressure")),
        "host_io_pressure": bool(host_assessment.get("io_pressure")),
    }
    return {
        "generated_at": generated_at.isoformat(),
        "project_id": project_id,
        "window_hours": float(hours),
        "decision_contract": list(VALID_DECISIONS),
        "assessment": assessment,
        "evidence": evidence,
        "policy": (
            "Recommend backpressure only for memory/IO host pressure or observed "
            "multi-worker diminishing returns; CPU/load-only saturation never triggers it."
        ),
        "privacy": (
            "bounded aggregate scaling/pressure evidence only; raw runner payloads, "
            "process metadata and command results are not exposed"
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Combine zCloud scaling and host pressure into read-only backpressure advice"
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "history.db",
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--hours", type=float, default=12.0)
    parser.add_argument("--proc-root", type=Path, default=Path("/proc"))
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)
    payload = report(
        args.db,
        project=args.project,
        hours=args.hours,
        proc_root=args.proc_root,
    )
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

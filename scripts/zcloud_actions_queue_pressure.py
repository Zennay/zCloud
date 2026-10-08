#!/usr/bin/env python3
"""Classify bounded GitHub Actions pressure on the canonical zCloud VPS runner."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys

MAX_INPUT_BYTES = 256 * 1024
MAX_RUNS = 30
MAX_JOBS_PER_RUN = 100
MAX_LABELS = 16
MAX_LABEL_LENGTH = 64
MAX_WORKFLOW_LENGTH = 160
MAX_FUTURE_SKEW_SECONDS = 60
STALL_SECONDS = 900

WAITING_STATES = {"queued", "waiting", "pending", "requested"}
ACTIVE_STATES = WAITING_STATES | {"in_progress"}
ALLOWED_STATES = ACTIVE_STATES | {"completed"}
TARGET_LABELS = {"self-hosted", "zcloud", "vps"}
WORKFLOW_RE = re.compile(r"^\.github/workflows/[A-Za-z0-9._-]+\.ya?ml$")


class PressureInputError(ValueError):
    pass


def _parse_time(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise PressureInputError(f"{field}_invalid")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise PressureInputError(f"{field}_invalid") from exc
    if parsed.tzinfo is None:
        raise PressureInputError(f"{field}_timezone_required")
    return parsed.astimezone(timezone.utc)


def _require_exact_keys(value: dict, expected: set[str], field: str) -> None:
    if set(value) != expected:
        raise PressureInputError(f"{field}_schema_invalid")


def _validate_workflow_path(value: object) -> str:
    if not isinstance(value, str) or len(value) > MAX_WORKFLOW_LENGTH:
        raise PressureInputError("workflow_path_invalid")
    if not WORKFLOW_RE.fullmatch(value):
        raise PressureInputError("workflow_path_invalid")
    return value


def validate_snapshot(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise PressureInputError("snapshot_not_object")
    _require_exact_keys(
        payload,
        {"schema_version", "captured_at", "capacity", "source_complete", "runs"},
        "snapshot",
    )
    if payload["schema_version"] != 1 or isinstance(payload["schema_version"], bool):
        raise PressureInputError("schema_version_invalid")
    capacity = payload["capacity"]
    if isinstance(capacity, bool) or not isinstance(capacity, int) or not 1 <= capacity <= 16:
        raise PressureInputError("capacity_invalid")
    if type(payload["source_complete"]) is not bool:
        raise PressureInputError("source_complete_invalid")

    captured_at = _parse_time(payload["captured_at"], "captured_at")
    runs = payload["runs"]
    if not isinstance(runs, list) or len(runs) > MAX_RUNS:
        raise PressureInputError("runs_invalid")

    normalized_runs: list[dict] = []
    for run in runs:
        if not isinstance(run, dict):
            raise PressureInputError("run_not_object")
        _require_exact_keys(run, {"workflow_path", "status", "created_at", "jobs"}, "run")
        workflow_path = _validate_workflow_path(run["workflow_path"])
        status = run["status"]
        if status not in ALLOWED_STATES:
            raise PressureInputError("run_status_invalid")
        created_at = _parse_time(run["created_at"], "run_created_at")
        if (created_at - captured_at).total_seconds() > MAX_FUTURE_SKEW_SECONDS:
            raise PressureInputError("run_created_after_capture")
        jobs = run["jobs"]
        if not isinstance(jobs, list) or len(jobs) > MAX_JOBS_PER_RUN:
            raise PressureInputError("jobs_invalid")

        normalized_jobs: list[dict] = []
        for job in jobs:
            if not isinstance(job, dict):
                raise PressureInputError("job_not_object")
            _require_exact_keys(job, {"status", "created_at", "labels"}, "job")
            job_status = job["status"]
            if job_status not in ALLOWED_STATES:
                raise PressureInputError("job_status_invalid")
            job_created = _parse_time(job["created_at"], "job_created_at")
            if (job_created - captured_at).total_seconds() > MAX_FUTURE_SKEW_SECONDS:
                raise PressureInputError("job_created_after_capture")
            labels = job["labels"]
            if not isinstance(labels, list) or len(labels) > MAX_LABELS:
                raise PressureInputError("job_labels_invalid")
            clean_labels: list[str] = []
            for label in labels:
                if (
                    not isinstance(label, str)
                    or not label
                    or len(label) > MAX_LABEL_LENGTH
                ):
                    raise PressureInputError("job_label_invalid")
                clean_labels.append(label)
            normalized_jobs.append(
                {"status": job_status, "created_at": job_created, "labels": clean_labels}
            )

        normalized_runs.append(
            {
                "workflow_path": workflow_path,
                "status": status,
                "created_at": created_at,
                "jobs": normalized_jobs,
            }
        )

    return {
        "captured_at": captured_at,
        "capacity": capacity,
        "source_complete": payload["source_complete"],
        "runs": normalized_runs,
    }


def classify_pressure(payload: object) -> dict:
    snapshot = validate_snapshot(payload)
    captured_at: datetime = snapshot["captured_at"]
    capacity: int = snapshot["capacity"]
    running = 0
    waiting = 0
    waiting_ages: list[int] = []
    waiting_workflows: set[str] = set()

    for run in snapshot["runs"]:
        for job in run["jobs"]:
            if not TARGET_LABELS.issubset(set(job["labels"])):
                continue
            if job["status"] == "in_progress":
                running += 1
            elif job["status"] in WAITING_STATES:
                waiting += 1
                age = int((captured_at - job["created_at"]).total_seconds())
                waiting_ages.append(max(0, age))
                waiting_workflows.add(Path(run["workflow_path"]).name)

    oldest_wait = max(waiting_ages) if waiting_ages else None
    reasons: list[str] = []
    state = "healthy"

    if not snapshot["source_complete"]:
        state = "incomplete"
        reasons.append("SOURCE_INCOMPLETE")
    elif running > capacity:
        state = "incomplete"
        reasons.append("RUNNING_EXCEEDS_CAPACITY")
    elif waiting == 0:
        state = "healthy"
    elif oldest_wait is not None and oldest_wait >= STALL_SECONDS:
        state = "stalled"
        reasons.append("WAIT_EXCEEDS_900S")
    elif running >= capacity or waiting > capacity * 2:
        state = "saturated"
        reasons.append("NO_IMMEDIATE_RUNNER_HEADROOM")
    else:
        state = "queued"
        reasons.append("BOUNDED_QUEUE_PRESENT")

    return {
        "schema_version": 1,
        "state": state,
        "reasons": reasons,
        "capacity": capacity,
        "running_self_hosted_jobs": running,
        "waiting_self_hosted_jobs": waiting,
        "headroom": max(0, capacity - running),
        "oldest_wait_seconds": oldest_wait,
        "waiting_workflows": sorted(waiting_workflows)[:8],
        "source_complete": snapshot["source_complete"],
        "mutation_performed": False,
    }


def load_snapshot(path: Path | None) -> object:
    if path is None:
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    else:
        if path.is_symlink() or not path.is_file():
            raise PressureInputError("input_path_invalid")
        if path.stat().st_size > MAX_INPUT_BYTES:
            raise PressureInputError("input_too_large")
        raw = path.read_bytes()
    if len(raw) > MAX_INPUT_BYTES:
        raise PressureInputError("input_too_large")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PressureInputError("input_json_invalid") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Classify read-only GitHub Actions queue pressure for zCloud VPS jobs"
    )
    parser.add_argument("--input", type=Path)
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args(argv)

    try:
        result = classify_pressure(load_snapshot(args.input))
    except (OSError, PressureInputError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 2

    print(json.dumps({"ok": True, **result}, sort_keys=True))
    if args.require_complete and result["state"] == "incomplete":
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

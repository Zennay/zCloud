#!/usr/bin/env python3
"""Fail-closed, observation-only FTMO runner recovery preflight."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

DIAGNOSTIC_POLICY = "zcloud-ftmo-research-runner-diagnostic-v2"
POLICY = "zcloud-ftmo-runner-recovery-preflight-v1"
EXPECTED_UNIT = "actions.runner.Zennay-Ftmo.vps-bb300bba-ftmo.service"

_TOP_LEVEL_KEYS = {
    "policy",
    "mutation_performed",
    "runner_service_count",
    "candidate_count",
    "status",
    "candidates",
}
_CANDIDATE_KEYS = {
    "unit",
    "active_state",
    "sub_state",
    "exec_main_status",
    "restart_count",
    "listener_count",
    "worker_count",
    "status",
    "recovery_advice",
}


class PreflightError(ValueError):
    pass


def _is_nonnegative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _deny(reason: str) -> dict[str, object]:
    return {
        "policy": POLICY,
        "decision": "deny_incomplete",
        "reason": reason,
        "mutation_authorized": False,
        "writer_action_candidate": "none",
    }


def classify(snapshot: dict[str, Any], *, same_job_live_evidence: bool) -> dict[str, object]:
    if not same_job_live_evidence:
        return _deny("fresh_live_evidence_not_proven")
    if set(snapshot) != _TOP_LEVEL_KEYS:
        return _deny("unexpected_top_level_schema")
    if snapshot.get("policy") != DIAGNOSTIC_POLICY:
        return _deny("diagnostic_policy_mismatch")
    if snapshot.get("mutation_performed") is not False:
        return _deny("diagnostic_mutation_boundary_invalid")

    candidates = snapshot.get("candidates")
    candidate_count = snapshot.get("candidate_count")
    service_count = snapshot.get("runner_service_count")
    if not isinstance(candidates, list):
        return _deny("candidate_inventory_invalid")
    if not _is_nonnegative_int(candidate_count) or candidate_count != len(candidates):
        return _deny("candidate_count_invalid")
    if not _is_nonnegative_int(service_count) or service_count < 1:
        return _deny("runner_service_inventory_invalid")
    if candidate_count != 1:
        return _deny("candidate_identity_not_unique")

    candidate = candidates[0]
    if not isinstance(candidate, dict) or set(candidate) != _CANDIDATE_KEYS:
        return _deny("candidate_schema_invalid")
    if candidate.get("unit") != EXPECTED_UNIT:
        return _deny("candidate_unit_mismatch")
    if snapshot.get("status") != candidate.get("status"):
        return _deny("aggregate_status_mismatch")

    listeners = candidate.get("listener_count")
    workers = candidate.get("worker_count")
    restarts = candidate.get("restart_count")
    if not all(_is_nonnegative_int(v) for v in (listeners, workers, restarts)):
        return _deny("runner_process_counters_invalid")

    active = candidate.get("active_state")
    status = candidate.get("status")
    advice = candidate.get("recovery_advice")

    if status == "orphaned_processes":
        if active == "active" or (listeners == 0 and workers == 0):
            return _deny("orphan_classification_incoherent")
        if advice != "reconcile_orphans_before_service_start":
            return _deny("orphan_recovery_advice_mismatch")
        return {
            "policy": POLICY,
            "decision": "deny_orphaned_processes",
            "reason": "inactive_service_has_residual_runner_processes",
            "mutation_authorized": False,
            "writer_action_candidate": "reconcile_orphans_first",
        }

    if status == "offline":
        if active == "active" or listeners != 0 or workers != 0:
            return _deny("offline_classification_incoherent")
        if advice != "service_start_candidate":
            return _deny("offline_recovery_advice_mismatch")
        return {
            "policy": POLICY,
            "decision": "start_candidate",
            "reason": "inactive_service_has_no_residual_runner_processes",
            "mutation_authorized": False,
            "writer_action_candidate": "guarded_service_start",
        }

    if status == "busy":
        if active != "active" or listeners != 1 or workers < 1:
            return _deny("busy_classification_incoherent")
        if advice != "leave_inflight_work_untouched":
            return _deny("busy_recovery_advice_mismatch")
        return {
            "policy": POLICY,
            "decision": "deny_inflight_work",
            "reason": "managed_runner_worker_is_active",
            "mutation_authorized": False,
            "writer_action_candidate": "leave_runner_untouched",
        }

    if status == "idle":
        if active != "active" or listeners != 1 or workers != 0:
            return _deny("idle_classification_incoherent")
        if advice != "runner_ready":
            return _deny("idle_recovery_advice_mismatch")
        return {
            "policy": POLICY,
            "decision": "no_recovery_needed",
            "reason": "managed_runner_is_idle",
            "mutation_authorized": False,
            "writer_action_candidate": "none",
        }

    if status == "degraded":
        if active != "active" or listeners == 1:
            return _deny("degraded_classification_incoherent")
        if advice != "inspect_listener_state":
            return _deny("degraded_recovery_advice_mismatch")
        return {
            "policy": POLICY,
            "decision": "deny_degraded",
            "reason": "managed_runner_listener_state_is_degraded",
            "mutation_authorized": False,
            "writer_action_candidate": "inspect_managed_listener",
        }

    return _deny("unsupported_diagnostic_status")


def _load_snapshot(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise PreflightError("snapshot must be a regular non-symlink file")
    if path.stat().st_size > 64 * 1024:
        raise PreflightError("snapshot exceeds bounded size")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PreflightError("snapshot is not strict UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise PreflightError("snapshot root must be an object")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument(
        "--same-job-live-evidence",
        action="store_true",
        help="Assert the snapshot was collected immediately earlier in this same guarded job.",
    )
    args = parser.parse_args()

    try:
        snapshot = _load_snapshot(args.snapshot)
        result = classify(snapshot, same_job_live_evidence=args.same_job_live_evidence)
    except PreflightError as exc:
        result = _deny(str(exc))

    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

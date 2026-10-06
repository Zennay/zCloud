"""Pure planner for the zSSH governance queue-priority cancellation policy.

This module never calls GitHub and never mutates Actions state. It exists so the
selection semantics can be tested independently before the mutating workflow is
wired to it after the serialized production window clears.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


GOVERNANCE_NAME = "zSSH main protection VPS apply"
CLOUDFLARE_PROBE_NAME = "zSSH Cloudflare credential metadata probe"
ALLOWLIST = frozenset(
    {
        "zSSH production origin readiness (zCloud lane)",
        "zSSH Caddy topology audit (zCloud lane)",
        "zSSH public gateway VPS preflight (zCloud lane)",
    }
)
MAX_RUNS = 100
RELEVANT_NAMES = ALLOWLIST | {GOVERNANCE_NAME, CLOUDFLARE_PROBE_NAME}


def _validated_id(run: dict[str, Any]) -> int:
    value = run.get("id")
    if type(value) is not int or value <= 0:
        raise ValueError("relevant run id must be a positive integer")
    return value


def _validated_timestamp(run: dict[str, Any]) -> datetime:
    value = run.get("created_at")
    if not isinstance(value, str) or not value:
        raise ValueError("relevant run created_at must be a non-empty string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("relevant run created_at must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ValueError("relevant run created_at must include timezone")
    return parsed


def plan_cancellations(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the bounded cancellation plan matching the current workflow policy."""
    if not isinstance(runs, list):
        raise TypeError("runs must be a list")
    if len(runs) > MAX_RUNS:
        raise ValueError("run inventory exceeds bounded policy")
    if any(not isinstance(run, dict) for run in runs):
        raise TypeError("every run must be an object")

    relevant = [run for run in runs if run.get("status") == "queued" and run.get("name") in RELEVANT_NAMES]
    for run in relevant:
        _validated_id(run)
        _validated_timestamp(run)

    governance = [
        run
        for run in runs
        if run.get("name") == GOVERNANCE_NAME and run.get("status") == "queued"
    ]
    cloudflare = [
        run
        for run in runs
        if run.get("name") == CLOUDFLARE_PROBE_NAME and run.get("status") == "queued"
    ]
    priority_runs = governance or cloudflare
    if not priority_runs:
        return {
            "schema_version": 1,
            "governance_run_present": False,
            "priority_run_present": False,
            "cancelled_run_ids": [],
            "cancelled_names": [],
            "reason": "no queued zSSH governance apply or Cloudflare credential metadata probe run",
        }

    target = max(priority_runs, key=_validated_timestamp)
    target_created = target["created_at"]
    target_time = _validated_timestamp(target)
    target_id = _validated_id(target)

    stale = [
        run
        for run in runs
        if run.get("status") == "queued"
        and run.get("name") in ALLOWLIST
        and _validated_timestamp(run) < target_time
    ]

    return {
        "schema_version": 1,
        "governance_run_present": target.get("name") == GOVERNANCE_NAME,
        "priority_run_present": True,
        "priority_run_name": target.get("name"),
        "priority_run_id": target_id,
        "priority_created_at": target_created,
        "cancelled_run_ids": [_validated_id(run) for run in stale],
        "cancelled_names": [run["name"] for run in stale],
        "policy": "queued-only older allowlisted zSSH read-only VPS audits behind governance or credential discovery",
    }

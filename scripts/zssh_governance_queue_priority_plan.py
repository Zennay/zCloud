"""Pure planner for the zSSH governance queue-priority cancellation policy.

This module never calls GitHub and never mutates Actions state. It exists so the
selection semantics can be tested independently before the mutating workflow is
wired to it after the serialized production window clears.
"""

from __future__ import annotations

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


def plan_cancellations(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the bounded cancellation plan matching the current workflow policy."""
    if not isinstance(runs, list):
        raise TypeError("runs must be a list")
    if len(runs) > MAX_RUNS:
        raise ValueError("run inventory exceeds bounded policy")
    if any(not isinstance(run, dict) for run in runs):
        raise TypeError("every run must be an object")

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

    target = max(priority_runs, key=lambda run: run.get("created_at") or "")
    target_created = target.get("created_at") or ""
    target_id = int(target["id"])

    stale = [
        run
        for run in runs
        if run.get("status") == "queued"
        and run.get("name") in ALLOWLIST
        and (run.get("created_at") or "") < target_created
    ]

    return {
        "schema_version": 1,
        "governance_run_present": target.get("name") == GOVERNANCE_NAME,
        "priority_run_present": True,
        "priority_run_name": target.get("name"),
        "priority_run_id": target_id,
        "priority_created_at": target_created,
        "cancelled_run_ids": [int(run["id"]) for run in stale],
        "cancelled_names": [run["name"] for run in stale],
        "policy": "queued-only older allowlisted zSSH read-only VPS audits behind governance or credential discovery",
    }

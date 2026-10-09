"""Pure, deny-first admission for *proposed* zCloud worker restart actions.

This module never restarts a browser or contacts a worker. Integration with any
runtime writer requires separate serialized ownership and exact-head validation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class RestartDecision:
    allowed: bool
    reason: str


def assess_restart(evidence: Mapping[str, object]) -> RestartDecision:
    """Require independently fresh, exact-generation and resource-safe evidence.

    A configured worker slot is not evidence of a healthy worker. An unknown
    condition always denies; never assume absent telemetry means idle.
    """
    required = (
        "worker_id", "generation_id", "observed_generation_id",
        "observation_age_seconds", "worker_active", "action_in_flight",
        "browser_healthy", "memory_safe", "lease_owned", "restart_authorized",
    )
    if any(k not in evidence for k in required):
        return RestartDecision(False, "missing_evidence")
    if any(not isinstance(evidence[k], str) or not evidence[k].strip()
           for k in ("worker_id", "generation_id", "observed_generation_id")):
        return RestartDecision(False, "invalid_identity")
    if evidence["generation_id"] != evidence["observed_generation_id"]:
        return RestartDecision(False, "generation_mismatch")
    age = evidence["observation_age_seconds"]
    if isinstance(age, bool) or not isinstance(age, (int, float)) or not (0 <= age <= 30):
        return RestartDecision(False, "stale_evidence")
    flags = ("worker_active", "action_in_flight", "browser_healthy",
             "memory_safe", "lease_owned", "restart_authorized")
    if any(type(evidence[k]) is not bool for k in flags):
        return RestartDecision(False, "invalid_flags")
    if evidence["worker_active"] or evidence["action_in_flight"]:
        return RestartDecision(False, "already_running")
    if not evidence["browser_healthy"]:
        return RestartDecision(False, "browser_unhealthy")
    if not evidence["memory_safe"]:
        return RestartDecision(False, "resource_guard")
    if not evidence["lease_owned"]:
        return RestartDecision(False, "claim_not_owned")
    if not evidence["restart_authorized"]:
        return RestartDecision(False, "authorization_missing")
    return RestartDecision(True, "admitted_offline_only")

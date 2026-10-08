"""Offline-only dashboard recovery authorization contract.

No GitHub API, shell, runner, service, database, or network operations.
This module never grants live recovery permission: it only models the
prerequisites for a *simulated* bounded plan used by unit tests.
"""
from dataclasses import dataclass
import re

_SHA = re.compile(r"[0-9a-f]{40}\Z")
_CORRELATION = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")


@dataclass(frozen=True)
class RecoveryRequest:
    event: str
    repository: str
    actor_trusted: bool
    ref: str
    target_sha: str
    canonical_sha: str
    owner_approved: bool
    approval_fresh: bool
    serialized_gates_released: bool
    dashboard_unhealthy: bool
    evidence_complete: bool
    recovery_in_flight: bool
    request_id: str


@dataclass(frozen=True)
class Decision:
    allowed_simulation: bool
    reason: str
    target_sha: str
    correlation_id: str

    @property
    def live_recovery_authorized(self):
        return False


def evaluate(req: RecoveryRequest) -> Decision:
    """Fail closed and emit a bounded, non-sensitive diagnostic."""
    sha = req.target_sha if isinstance(req.target_sha, str) and _SHA.fullmatch(req.target_sha) else "invalid"
    correlation = req.request_id if isinstance(req.request_id, str) and _CORRELATION.fullmatch(req.request_id) else "invalid"
    reasons = (
        (req.event != "workflow_dispatch", "untrusted_event"),
        (req.repository != "Zennay/zCloud", "untrusted_repository"),
        (req.actor_trusted is not True, "untrusted_actor"),
        (req.ref != "refs/heads/main", "untrusted_ref"),
        (sha == "invalid" or not isinstance(req.canonical_sha, str) or not _SHA.fullmatch(req.canonical_sha), "invalid_sha"),
        (req.target_sha != req.canonical_sha, "stale_sha"),
        (req.owner_approved is not True, "approval_missing"),
        (req.approval_fresh is not True, "approval_expired"),
        (req.serialized_gates_released is not True, "serialized_gate_closed"),
        (req.dashboard_unhealthy is not True, "dashboard_healthy_or_unknown"),
        (req.evidence_complete is not True, "evidence_incomplete"),
        (req.recovery_in_flight is not False, "recovery_conflict"),
        (correlation == "invalid", "invalid_correlation"),
    )
    reason = next((label for denied, label in reasons if denied), "simulated_plan_only")
    return Decision(reason == "simulated_plan_only", reason, sha, correlation)

#!/usr/bin/env python3
"""Offline decision-model fixture for issue #1135. Never operates on a VPS.

This is NOT the live workflow authorization implementation. It documents
expected acceptance behavior for the future workflow owner.
"""
from dataclasses import dataclass
import json
import sys


@dataclass(frozen=True)
class Evidence:
    event: str = "pull_request"
    canonical_repository: bool = False
    current_main: bool = False
    trusted_actor: bool = False
    guarded_runner: bool = False
    gate_580_released: bool = False
    gate_1089_released: bool = False
    external_healthy: bool | None = None
    independent_unhealthy_proof: bool = False
    explicit_approval: bool = False
    rollback_receipt: bool = False


def decide(e: Evidence) -> str:
    """Return a fail-closed result, never a command or mutation."""
    if e.event != "workflow_dispatch":
        return "DENY_PR_MUTATION"
    if e.external_healthy is True:
        return "NOOP_HEALTHY"
    if e.external_healthy is None or not e.independent_unhealthy_proof:
        return "READ_ONLY_TRIAGE"
    if not all((e.canonical_repository, e.current_main, e.trusted_actor,
                e.guarded_runner, e.gate_580_released, e.gate_1089_released,
                e.explicit_approval, e.rollback_receipt)):
        return "DENY_MISSING_GATE"
    return "ELIGIBLE_SEPARATE_APPROVAL"


def self_test() -> None:
    eligible = Evidence(event="workflow_dispatch", canonical_repository=True,
                        current_main=True, trusted_actor=True, guarded_runner=True,
                        gate_580_released=True, gate_1089_released=True,
                        external_healthy=False, independent_unhealthy_proof=True,
                        explicit_approval=True, rollback_receipt=True)
    assert decide(eligible) == "ELIGIBLE_SEPARATE_APPROVAL"
    for event in ("pull_request", "push", "workflow_run", ""):
        assert decide(Evidence(**{**eligible.__dict__, "event": event})) == "DENY_PR_MUTATION"
    assert decide(Evidence(**{**eligible.__dict__, "external_healthy": True})) == "NOOP_HEALTHY"
    assert decide(Evidence(**{**eligible.__dict__, "external_healthy": None})) == "READ_ONLY_TRIAGE"
    assert decide(Evidence(**{**eligible.__dict__, "independent_unhealthy_proof": False})) == "READ_ONLY_TRIAGE"
    for name in ("canonical_repository", "current_main", "trusted_actor",
                 "guarded_runner", "gate_580_released", "gate_1089_released",
                 "explicit_approval", "rollback_receipt"):
        assert decide(Evidence(**{**eligible.__dict__, name: False})) == "DENY_MISSING_GATE", name


if __name__ == "__main__":
    self_test()
    print(json.dumps({"contract": "dashboard-recovery-admission-v1",
                      "result": "PASS", "execution": "offline-no-mutation"}))

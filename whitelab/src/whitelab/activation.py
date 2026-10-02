from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from secrets import token_urlsafe

from .models import Target
from .scope import ScopePolicy


class ActivationMode(str, Enum):
    PLAN_ONLY = "plan_only"
    LAB_ONLY = "lab_only"
    AUTHORIZED = "authorized"


@dataclass(frozen=True)
class ActivationPolicy:
    mode: ActivationMode = ActivationMode.PLAN_ONLY
    activation_reference: str | None = None

    def validate(self) -> None:
        if self.mode is ActivationMode.AUTHORIZED and not self.activation_reference:
            raise ValueError("authorized mode requires an explicit activation_reference")


@dataclass(frozen=True)
class ExecutionPermit:
    permit_id: str
    target: str
    capability_id: str
    mode: ActivationMode
    activation_reference: str
    issued_at: datetime


class ActivationGate:
    """Policy gate for future active adapters.

    M0 ships no active adapters. This gate exists now so future adapters cannot
    invent their own activation rules.
    """

    def __init__(self, scope_policy: ScopePolicy, activation_policy: ActivationPolicy):
        activation_policy.validate()
        self.scope_policy = scope_policy
        self.activation_policy = activation_policy

    def issue(self, target: Target, capability_id: str) -> ExecutionPermit:
        decision = self.scope_policy.decide(target)
        if not decision.allowed:
            raise PermissionError(f"target denied by scope gate: {decision.reason.value}")

        if self.activation_policy.mode is ActivationMode.PLAN_ONLY:
            raise PermissionError("active execution is disabled: project is plan-only")

        if self.activation_policy.mode is ActivationMode.LAB_ONLY:
            if decision.reason.value not in {"loopback", "private_lab"}:
                raise PermissionError("lab-only mode cannot issue permits for public targets")
            reference = self.activation_policy.activation_reference or "LAB"
        else:
            if target.authorization is None or not target.authorization.is_current():
                raise PermissionError("authorized execution requires current target authorization")
            reference = self.activation_policy.activation_reference or target.authorization.reference

        return ExecutionPermit(
            permit_id=token_urlsafe(24),
            target=decision.normalized_host or target.value,
            capability_id=capability_id,
            mode=self.activation_policy.mode,
            activation_reference=reference,
            issued_at=datetime.now(timezone.utc),
        )

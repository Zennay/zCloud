from __future__ import annotations

from dataclasses import asdict, dataclass

from .capabilities import CapabilityState, get_capabilities
from .models import Target
from .scope import ScopeDecision, ScopePolicy


class ExecutionDisabled(RuntimeError):
    pass


@dataclass(frozen=True)
class AssessmentPlan:
    target: str
    scope: ScopeDecision
    capability_ids: tuple[str, ...]
    execution_enabled: bool = False

    def to_dict(self) -> dict:
        data = asdict(self)
        data["scope"]["reason"] = self.scope.reason.value
        return data


class Planner:
    def __init__(self, scope_policy: ScopePolicy):
        self.scope_policy = scope_policy

    def build(self, target: Target) -> AssessmentPlan:
        decision = self.scope_policy.decide(target)
        if not decision.allowed:
            return AssessmentPlan(target.value, decision, ())
        capabilities = tuple(
            item.capability_id
            for item in get_capabilities()
            if item.state in {CapabilityState.PLANNING, CapabilityState.LAB_ONLY}
        )
        return AssessmentPlan(target.value, decision, capabilities)


class NetworkExecutor:
    """M0 hard stop: active target interaction does not exist yet."""

    def execute(self, *_args, **_kwargs):
        raise ExecutionDisabled(
            "LightUp M0 is plan-only. Active network execution requires a later explicit activation."
        )

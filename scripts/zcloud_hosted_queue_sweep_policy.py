from __future__ import annotations

from dataclasses import dataclass


MIN_QUEUED_AGE_SECONDS = 480
MIN_IN_PROGRESS_AGE_SECONDS = 1800

# Auto-cancellation is deliberately limited to known observational/proof lanes.
# Unknown workflows fail closed and remain untouched.
SAFE_CANCEL_FRAGMENTS = (
    "vps-cpu-diagnostic",
    "zcloud-vps-execution-probe",
    "zcloud-runner-cross-lane-proof",
    "zcloud-state-receipt-coverage-audit",
    "zcloud-violentmonkey-vps-validation",
    "zcloud-oom-guard-vps-validation",
    "lightup-vps-verification",
    "zguard-vps-verification",
    "ulab-zcloud-dogfood",
)

# These tokens identify workflows whose cancellation can interrupt a live
# mutation, recovery or release operation. They remain protected even if a
# future name also matches a diagnostic fragment.
PROTECTED_MUTATION_FRAGMENTS = (
    "deploy",
    "recovery",
    "restart",
    "activate",
    "activation",
    "apply",
    "bootstrap",
    "refresh",
    "reconcile",
    "watchdog",
    "promotion",
    "rollback",
    "release",
)


@dataclass(frozen=True)
class SweepDecision:
    cancel: bool
    reason: str


def classify_run(*, path: str, name: str, status: str, age_seconds: int) -> SweepDecision:
    haystack = f"{path} {name}".lower()
    normalized_status = str(status or "").strip().lower()
    age = max(0, int(age_seconds))

    if any(fragment in haystack for fragment in PROTECTED_MUTATION_FRAGMENTS):
        return SweepDecision(False, "protected_mutation")

    if not any(fragment in haystack for fragment in SAFE_CANCEL_FRAGMENTS):
        return SweepDecision(False, "unclassified_fail_closed")

    if normalized_status == "queued":
        if age < MIN_QUEUED_AGE_SECONDS:
            return SweepDecision(False, "recent_queued")
        return SweepDecision(True, "stale_queued_safe_lane")

    if normalized_status == "in_progress":
        if age < MIN_IN_PROGRESS_AGE_SECONDS:
            return SweepDecision(False, "active_safe_lane_within_budget")
        return SweepDecision(True, "stale_in_progress_safe_lane")

    return SweepDecision(False, "unsupported_status")

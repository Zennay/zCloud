"""Pure, offline decision contract for zCloud production-status recovery.

No GitHub calls, no writes, no production authorization. Integration requires
serialized release-owner approval (issue #1216).
"""
from dataclasses import dataclass

ACTIVE = frozenset({"queued", "in_progress", "pending", "waiting", "requested"})


@dataclass(frozen=True)
class Run:
    sha: str
    branch: str
    event: str
    status: str
    conclusion: str = ""


def decide_rearm(
    *, sha: str, production_status_present: bool,
    deploy_runs: tuple[Run, ...], regression_runs: tuple[Run, ...],
    explicit_retry_approved: bool = False,
) -> str:
    """Return a deny-first decision; never dispatch any work.

    The SHA-bound completed recovery workflow_dispatch is a durable dedupe
    marker. An operator-approved retry re-arms only after active work clears;
    it does not bypass a successful regression or an active deploy.
    """
    if not sha or not all(ch in "0123456789abcdef" for ch in sha.lower()) or len(sha) != 40:
        return "deny_invalid_sha"
    if production_status_present:
        return "noop_production_status"
    deploys = tuple(r for r in deploy_runs if r.sha == sha and r.branch == "main")
    if any(r.status in ACTIVE for r in deploys):
        return "wait_active_deploy"
    if any(r.status == "completed" and r.conclusion in {"failure", "cancelled", "timed_out"} for r in deploys) and not explicit_retry_approved:
        return "deny_failed_deploy"
    regressions = tuple(r for r in regression_runs if r.sha == sha and r.branch == "main")
    if any(r.status in ACTIVE for r in regressions):
        return "wait_active_regression"
    if not any(r.conclusion == "success" and r.status == "completed" and r.event in {"push", "workflow_dispatch"} for r in regressions):
        return "wait_exact_green"
    if not explicit_retry_approved and any(r.status == "completed" and r.event == "workflow_dispatch" for r in regressions):
        return "noop_already_rearmed"
    return "eligible_for_owner_review"

#!/usr/bin/env python3
"""Explain whether one proposed recovery action is ready for human review.

This is a local, advisory policy evaluator. It does not read live services,
contact GitHub/Notion, acquire claims, retry CI, merge PRs, deploy or restart
runners. Operator-supplied evidence is never treated as independently verified.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import stat
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse, urlunsplit

MAX_INPUT_BYTES = 256 * 1024
MAX_OUTPUT_BYTES = 128 * 1024
MAX_BATCH_REQUESTS = 25
UTC = timezone.utc
SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
ACTIONS = frozenset({
    "retry_ci", "route_root_fix", "assign_independent_work", "park_blocker",
    "escalate_human", "merge_pr", "deploy", "restart_runner",
})
ROOT_FIELDS = frozenset({
    "schema_version", "action", "project_id", "incident_id",
    "observed_at_utc", "source_url", "owner", "evidence",
})
EVIDENCE_FIELDS = frozenset({
    "source_verified", "failure_kind", "attempts", "retry_budget",
    "backoff_elapsed", "active_claim", "independent_task_ready",
    "scope_disjoint", "dependency_kind", "dependency_scope",
    "dependency_unchanged", "escalation_reason", "permission_required",
    "irreversible_risk", "safe_routes_exhausted", "capacity", "exact_head",
    "required_ci", "independent_review", "branch_protection", "owner_authorized",
    "root_cause_confirmed", "unresolved_review_threads", "merged_main",
    "post_merge_health", "release_approval", "runtime_owner_authorized",
    "runner_state", "running_jobs", "human_approval",
})
ENUMS = {
    "failure_kind": {"transient", "deterministic", "unknown"},
    "active_claim": {"none", "active", "unknown"},
    "capacity": {"healthy", "pressured", "unavailable", "unknown"},
    "exact_head": {"verified", "changed", "unknown"},
    "required_ci": {"green", "red", "pending", "unknown"},
    "independent_review": {"approved", "changes_requested", "missing", "unknown"},
    "branch_protection": {"satisfied", "blocked", "unknown"},
    "merged_main": {"verified", "no", "unknown"},
    "post_merge_health": {"healthy", "failed", "unknown"},
    "runner_state": {"healthy", "unavailable", "unknown"},
    "running_jobs": {"none", "active", "unknown"},
    "dependency_kind": {"external", "internal", "unknown"},
    "dependency_scope": {"single_task", "project_wide", "unknown"},
    "escalation_reason": {"permission_required", "irreversible_risk", "safe_routes_exhausted", "unknown"},
}
BOOL_FIELDS = {
    "source_verified", "backoff_elapsed", "independent_task_ready",
    "scope_disjoint", "dependency_unchanged", "permission_required",
    "irreversible_risk", "safe_routes_exhausted", "owner_authorized",
    "root_cause_confirmed", "release_approval", "runtime_owner_authorized",
    "human_approval",
}


class PolicyInputError(ValueError):
    """Input cannot be interpreted safely."""


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PolicyInputError("duplicate JSON property")
        result[key] = value
    return result


def _reject_constant(_value):
    raise PolicyInputError("non-standard JSON numeric constant")


def _read_input(path):
    if path == "-":
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            raise PolicyInputError("input exceeds 256 KiB")
        return raw
    source = Path(path)
    before = source.lstat()
    if not stat.S_ISREG(before.st_mode) or source.is_symlink():
        raise PolicyInputError("input must be a regular non-symlink file")
    if before.st_size > MAX_INPUT_BYTES:
        raise PolicyInputError("input exceeds 256 KiB")
    if not hasattr(os, "O_NOFOLLOW"):
        raise PolicyInputError("secure no-follow file opening is unavailable")
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened = os.fstat(fd)
        if (not stat.S_ISREG(opened.st_mode)
                or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
                or opened.st_size > MAX_INPUT_BYTES):
            raise PolicyInputError("input identity changed before reading")
        with os.fdopen(os.dup(fd), "rb") as stream:
            raw = stream.read(MAX_INPUT_BYTES + 1)
        after = os.fstat(fd)
        if (len(raw) > MAX_INPUT_BYTES
                or opened.st_size != after.st_size
                or opened.st_mtime_ns != after.st_mtime_ns):
            raise PolicyInputError("input changed or exceeded its bound")
        return raw
    finally:
        os.close(fd)


def _timestamp(value):
    if type(value) is not str or len(value) > 45:
        raise PolicyInputError("observed_at_utc must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PolicyInputError("observed_at_utc is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PolicyInputError("observed_at_utc must include a timezone")
    parsed = parsed.astimezone(UTC)
    if parsed > datetime.now(UTC) + timedelta(minutes=2):
        raise PolicyInputError("observed_at_utc is too far in the future")
    return parsed


def _source_url(value):
    if type(value) is not str or len(value) > 500:
        raise PolicyInputError("source_url must be a canonical HTTPS source link")
    # Source links are repeated in Markdown handoffs. Raw control/whitespace
    # characters can break the literal HTML-code line and inject new Markdown.
    # Require a canonical, single-line URL before any renderer sees it.
    if any(char.isspace() or ord(char) < 0x20 or ord(char) == 0x7f for char in value):
        raise PolicyInputError("source_url must not contain whitespace or control characters")
    try:
        parsed = urlparse(value)
    except ValueError as exc:
        raise PolicyInputError("source_url is malformed") from exc
    if (parsed.scheme != "https" or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.hostname.lower() not in {
                "github.com", "app.notion.com", "www.notion.so", "notion.so",
            }):
        raise PolicyInputError("source_url must use an approved HTTPS host")
    # Query strings and fragments are not part of the source identity. They may
    # contain auth tokens or tracking data, so never echo them into receipts.
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", "", ""))


def _validate(data):
    if type(data) is not dict:
        raise PolicyInputError("top-level JSON value must be an object")
    extra = set(data) - ROOT_FIELDS
    if extra:
        raise PolicyInputError("unknown top-level properties: " + ", ".join(sorted(extra)))
    missing = ROOT_FIELDS - set(data)
    if missing:
        raise PolicyInputError("missing top-level properties: " + ", ".join(sorted(missing)))
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise PolicyInputError("schema_version must be integer 1")
    if type(data["action"]) is not str or data["action"] not in ACTIONS:
        raise PolicyInputError("action must be one of: " + ", ".join(sorted(ACTIONS)))
    for field in ("project_id", "incident_id"):
        value = data[field]
        if type(value) is not str or not SLUG.fullmatch(value):
            raise PolicyInputError(field + " must be a bounded ASCII identifier")
    owner = data["owner"]
    if owner is not None and (type(owner) is not str or not SLUG.fullmatch(owner)):
        raise PolicyInputError("owner must be null or a bounded ASCII identifier")
    observed = _timestamp(data["observed_at_utc"])
    source = _source_url(data["source_url"])
    evidence = data["evidence"]
    if type(evidence) is not dict:
        raise PolicyInputError("evidence must be an object")
    extra_evidence = set(evidence) - EVIDENCE_FIELDS
    if extra_evidence:
        raise PolicyInputError("unknown evidence properties: " + ", ".join(sorted(extra_evidence)))
    for field in BOOL_FIELDS:
        if field in evidence and evidence[field] is not None and type(evidence[field]) is not bool:
            raise PolicyInputError(field + " must be true, false or null")
    for field, allowed in ENUMS.items():
        if field in evidence and evidence[field] is not None and (
            type(evidence[field]) is not str or evidence[field] not in allowed
        ):
            raise PolicyInputError(field + " has an unsupported value")
    for field in ("attempts", "retry_budget", "unresolved_review_threads"):
        value = evidence.get(field)
        if value is not None and (
            type(value) is not int or value < 0 or value > 10000
        ):
            raise PolicyInputError(field + " must be an integer from 0 to 10000")
    return observed, source, evidence


def evaluate(data, max_age_minutes, *, now=None):
    if type(max_age_minutes) is not int or not 1 <= max_age_minutes <= 1440:
        raise PolicyInputError("max_age_minutes must be an integer from 1 to 1440")
    observed, source, evidence = _validate(data)
    now = datetime.now(UTC) if now is None else now
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise PolicyInputError("evaluation clock must be timezone-aware")
    now = now.astimezone(UTC)
    age = now - observed
    blockers = []
    requirements = []

    def require(condition, code, message, fields=()):
        if condition is True:
            return
        blockers.append({"code": code, "message": message})
        requirements.extend(field for field in fields if field not in requirements)

    def value(name):
        return evidence.get(name)

    require(age <= timedelta(minutes=max_age_minutes),
            "source_stale", "Source observation is older than the configured freshness window.",
            ("observed_at_utc",))
    require(value("source_verified") is True,
            "source_not_verified", "The supplied source receipt is not marked verified.",
            ("evidence.source_verified",))
    action = data["action"]

    if action == "retry_ci":
        require(value("failure_kind") == "transient",
                "not_transient", "Only a classified transient failure is eligible for bounded retry.",
                ("evidence.failure_kind",))
        require(value("required_ci") == "red",
                "ci_not_terminal_red", "A retry needs a terminal failing CI result, not green or pending CI.",
                ("evidence.required_ci",))
        require(value("exact_head") == "verified",
                "head_not_verified", "The failure must belong to the verified exact PR head.",
                ("evidence.exact_head",))
        attempts, budget = value("attempts"), value("retry_budget")
        require(type(attempts) is int and type(budget) is int and attempts < budget,
                "retry_budget_exhausted_or_unknown",
                "A numeric remaining retry budget is required; exhausted budgets cannot be retried.",
                ("evidence.attempts", "evidence.retry_budget"))
        require(value("backoff_elapsed") is True,
                "backoff_not_elapsed", "The bounded retry backoff must have elapsed.",
                ("evidence.backoff_elapsed",))
        require(value("active_claim") == "none",
                "claim_not_clear", "Do not retry through an active or unknown work claim.",
                ("evidence.active_claim",))
        require(value("capacity") == "healthy",
                "capacity_not_healthy", "Runner capacity must be observed healthy before a retry is considered.",
                ("evidence.capacity",))
        require(value("owner_authorized") is True,
                "owner_authorization_missing", "An authorized owner must approve the recovery route.",
                ("evidence.owner_authorized",))

    elif action == "route_root_fix":
        require(value("failure_kind") == "deterministic",
                "failure_not_deterministic", "Root-fix routing requires a deterministic failure classification.",
                ("evidence.failure_kind",))
        require(value("root_cause_confirmed") is True,
                "root_cause_unconfirmed", "A concrete root cause must be confirmed before routing a fix.",
                ("evidence.root_cause_confirmed",))
        require(data["owner"] is not None,
                "owner_missing", "A concrete responsible owner is required; do not invent ownership.",
                ("owner",))
        require(value("active_claim") == "none",
                "claim_not_clear", "An active or unknown claim must not be displaced.",
                ("evidence.active_claim",))
        require(value("owner_authorized") is True,
                "owner_authorization_missing", "The destination owner must be authorized for this scope.",
                ("evidence.owner_authorized",))

    elif action == "assign_independent_work":
        require(value("independent_task_ready") is True,
                "task_not_ready", "Independent work must be explicitly identified as ready.",
                ("evidence.independent_task_ready",))
        require(value("scope_disjoint") is True,
                "scope_overlap_unknown", "Disjointness from active work must be positively established.",
                ("evidence.scope_disjoint",))
        require(value("active_claim") == "none",
                "claim_not_clear", "An active or unknown claim must not be displaced.",
                ("evidence.active_claim",))
        require(value("capacity") == "healthy",
                "capacity_not_healthy", "Worker capacity must be observed healthy before admission.",
                ("evidence.capacity",))
        require(value("owner_authorized") is True,
                "owner_authorization_missing", "An authorized owner must approve task admission.",
                ("evidence.owner_authorized",))

    elif action == "park_blocker":
        require(value("dependency_kind") == "external",
                "dependency_not_external", "Only an external dependency may be parked by this action.",
                ("evidence.dependency_kind",))
        require(value("dependency_scope") == "single_task",
                "dependency_scope_too_broad", "Park only the dependent task, never an entire project lane.",
                ("evidence.dependency_scope",))
        require(value("dependency_unchanged") is True,
                "dependency_not_reconfirmed", "The specific external blocker must be confirmed unchanged.",
                ("evidence.dependency_unchanged",))
        require(value("active_claim") == "none",
                "claim_not_clear", "Do not alter an active or unknown work claim.",
                ("evidence.active_claim",))
        require(value("owner_authorized") is True,
                "owner_authorization_missing", "An authorized owner must approve parking the dependent task.",
                ("evidence.owner_authorized",))
        require(value("independent_task_ready") is True,
                "independent_work_not_ready", "Identify unrelated ready work before parking the dependency.",
                ("evidence.independent_task_ready",))
        require(value("scope_disjoint") is True,
                "scope_overlap_unknown", "The continuing work must be positively disjoint from the blocked task.",
                ("evidence.scope_disjoint",))
        require(value("capacity") == "healthy",
                "capacity_not_healthy", "Healthy capacity is required to continue independent work.",
                ("evidence.capacity",))

    elif action == "escalate_human":
        reason = value("escalation_reason")
        require(reason in {"permission_required", "irreversible_risk", "safe_routes_exhausted"},
                "escalation_reason_missing",
                "Escalation is limited to a permission gate, irreversible/high-risk decision, or exhausted safe routes.",
                ("evidence.escalation_reason",))
        if reason == "permission_required":
            require(value("permission_required") is True,
                    "permission_gate_unconfirmed", "The specific required permission must be identified.",
                    ("evidence.permission_required",))
        elif reason == "irreversible_risk":
            require(value("irreversible_risk") is True,
                    "irreversible_risk_unconfirmed", "The irreversible or high-risk decision must be explicit.",
                    ("evidence.irreversible_risk",))
        elif reason == "safe_routes_exhausted":
            require(value("safe_routes_exhausted") is True,
                    "safe_routes_not_exhausted", "Escalate only after safe recovery routes are exhausted.",
                    ("evidence.safe_routes_exhausted",))
        require(data["owner"] is not None,
                "human_owner_missing", "Name the authorized human or owner for escalation.",
                ("owner",))
        require(value("active_claim") in {"none", "active"},
                "claim_state_unknown", "Resolve claim state before routing escalation; do not take over unknown work.",
                ("evidence.active_claim",))

    elif action == "merge_pr":
        require(value("exact_head") == "verified",
                "head_not_verified", "Required gates must be bound to the current exact PR head.",
                ("evidence.exact_head",))
        require(value("required_ci") == "green",
                "required_ci_not_green", "Every required check must be green on the exact head.",
                ("evidence.required_ci",))
        require(value("independent_review") == "approved",
                "independent_review_missing", "An independent approving review is required.",
                ("evidence.independent_review",))
        require(value("branch_protection") == "satisfied",
                "branch_protection_not_satisfied", "GitHub branch protection must be satisfied normally.",
                ("evidence.branch_protection",))
        require(value("unresolved_review_threads") == 0,
                "review_threads_open_or_unknown", "No unresolved review threads may remain.",
                ("evidence.unresolved_review_threads",))
        require(value("merged_main") == "no",
                "main_state_not_open", "The source must be verified as not already merged.",
                ("evidence.merged_main",))
        require(value("owner_authorized") is True,
                "owner_authorization_missing", "The authorized PR owner must approve integration.",
                ("evidence.owner_authorized",))

    elif action == "deploy":
        require(value("exact_head") == "verified",
                "head_not_verified", "Release evidence must be bound to an exact source head.",
                ("evidence.exact_head",))
        require(value("required_ci") == "green",
                "required_ci_not_green", "All required CI must be green on the exact release source.",
                ("evidence.required_ci",))
        require(value("independent_review") == "approved",
                "independent_review_missing", "An independent approving review is required.",
                ("evidence.independent_review",))
        require(value("branch_protection") == "satisfied",
                "branch_protection_not_satisfied", "Normal branch protection must be satisfied.",
                ("evidence.branch_protection",))
        require(value("merged_main") == "verified",
                "main_merge_unverified", "Deployment requires proof of the exact source on main.",
                ("evidence.merged_main",))
        require(value("post_merge_health") == "healthy",
                "main_health_unverified", "Current-main health must be observed healthy after merge.",
                ("evidence.post_merge_health",))
        require(value("release_approval") is True,
                "release_approval_missing", "A release owner must explicitly approve deployment.",
                ("evidence.release_approval",))
        require(value("runtime_owner_authorized") is True,
                "runtime_owner_missing", "Runtime-owner authorization is required for deployment.",
                ("evidence.runtime_owner_authorized",))
        require(value("capacity") == "healthy",
                "capacity_not_healthy", "Runtime capacity must be observed healthy before release.",
                ("evidence.capacity",))

    elif action == "restart_runner":
        require(value("runner_state") == "unavailable",
                "runner_unavailability_unproven", "Restart consideration requires an observed unavailable runner.",
                ("evidence.runner_state",))
        require(value("running_jobs") == "none",
                "running_jobs_unknown_or_active", "Running jobs must be positively observed absent.",
                ("evidence.running_jobs",))
        require(value("root_cause_confirmed") is True,
                "root_cause_unconfirmed", "The listener failure cause must be confirmed.",
                ("evidence.root_cause_confirmed",))
        require(value("runtime_owner_authorized") is True,
                "runtime_owner_missing", "Only an authorized runtime owner may approve runner recovery.",
                ("evidence.runtime_owner_authorized",))
        require(value("human_approval") is True,
                "human_approval_missing", "Runner restart requires explicit human approval.",
                ("evidence.human_approval",))

    decision = "READY_FOR_HUMAN_REVIEW" if not blockers else "HOLD"
    return {
        "schema_version": 1,
        "policy": "zsentinel-recovery-policy-v1",
        "project_id": data["project_id"],
        "incident_id": data["incident_id"],
        "action": action,
        "decision": decision,
        "observed_at_utc": observed.isoformat().replace("+00:00", "Z"),
        "source_url": source,
        "source_age_seconds": max(0, int(age.total_seconds())),
        "source_freshness_window_minutes": max_age_minutes,
        "owner": data["owner"],
        "blockers": blockers,
        "required_evidence": requirements,
        "advisory_only": True,
        "evidence_independently_verified": False,
        "actions_executed": False,
        "authority": {
            "claim_acquired": False,
            "ci_retried": False,
            "merge_executed": False,
            "deployment_executed": False,
            "runner_restarted": False,
        },
    }


# Human-review ordering only; never infer incident severity or live authority.
WORKORDER_ORDER = {
    "merge_pr": 0, "restart_runner": 1, "route_root_fix": 2,
    "retry_ci": 3, "deploy": 4, "escalate_human": 5,
    "park_blocker": 6, "assign_independent_work": 7,
}


def _batch_workorders(results):
    """One bounded non-authorizing handoff per incident, with collision HOLDs."""
    groups = {}
    for item in results:
        report = item["report"]
        key = (report["project_id"], report["incident_id"])
        groups.setdefault(key, []).append(item)

    workorders = []
    for (project, incident), items in groups.items():
        actions = sorted((item["report"]["action"] for item in items),
                         key=lambda action: (WORKORDER_ORDER[action], action))
        conflict = len(actions) > 1
        if conflict:
            for item in items:
                report = item["report"]
                report["decision"] = "HOLD"
                report["blockers"].append({
                    "code": "competing_incident_actions",
                    "message": "Multiple recovery actions target one incident; "
                               "an authorized owner must select one route.",
                })
                if "owner_route_selection" not in report["required_evidence"]:
                    report["required_evidence"].append("owner_route_selection")
        reports = [item["report"] for item in items]
        blockers = sorted({hold["code"] for report in reports
                           for hold in report["blockers"]})
        owners = {report["owner"] for report in reports}
        workorders.append({
            "project_id": project,
            "incident_id": incident,
            "input_indexes": [item["index"] for item in items],
            "proposed_actions": actions,
            "owner": next(iter(owners)) if len(owners) == 1 else None,
            "decision": "HOLD" if blockers else "READY_FOR_HUMAN_REVIEW",
            "problem_codes": blockers,
            "source_urls": sorted({report["source_url"] for report in reports}),
            "next_step": ("select_one_authorized_action" if conflict else
                          "resolve_evidence_holds" if blockers else
                          "verify_live_gates_and_obtain_owner_approval"),
            "human_review_required": True,
            "actions_executed": False,
        })
    return sorted(workorders, key=lambda item: (
        min(WORKORDER_ORDER[action] for action in item["proposed_actions"]),
        item["project_id"], item["incident_id"],
    ))


def workorders_text(batch):
    """Compact three-line per-incident owner handoff, not a job dispatcher."""
    lines = [
        "zSentinel | advisory incident workorders",
        "Observed: " + batch["evaluated_at_utc"] +
        " | Incident groups: " + str(batch["incident_count"]) +
        " | No independent source verification or execution.",
        "",
    ]
    for order in batch["owner_workorders"]:
        issues = ", ".join(order["problem_codes"][:4]) or "no local policy holds"
        if len(order["problem_codes"]) > 4:
            issues += ", ..."
        lines.extend([
            "Problem [" + order["project_id"] + "/" + order["incident_id"] + "]: " + issues,
            "Action: " + order["next_step"] + " | proposal: " +
            ", ".join(order["proposed_actions"]) +
            " | owner: " + (order["owner"] or "UNKNOWN"),
            "Status: " + order["decision"] +
            " | human approval required; no claim/retry/merge/deploy performed.",
            "",
        ])
    return "\n".join(lines)


def evaluate_batch(data, max_age_minutes):
    """Evaluate a bounded set of proposals against one shared clock snapshot."""
    if type(data) is not list or not 1 <= len(data) <= MAX_BATCH_REQUESTS:
        raise PolicyInputError("batch input must be a JSON array of 1 to 25 requests")

    evaluated_at = datetime.now(UTC)
    results = []
    seen = set()
    for index, item in enumerate(data):
        report = evaluate(item, max_age_minutes, now=evaluated_at)
        identity = (report["project_id"], report["incident_id"], report["action"])
        if identity in seen:
            raise PolicyInputError(
                "batch contains duplicate project/incident/action entries"
            )
        seen.add(identity)
        results.append({"index": index, "report": report})

    # Multiple possible actions for the same incident are alternatives,
    # not independent authorizations. Never publish simultaneous READY
    # decisions for one unresolved incident (e.g. retry and root-fix).
    by_incident = {}
    for item in results:
        report = item["report"]
        by_incident.setdefault((report["project_id"], report["incident_id"]), []).append(item)
    for proposals in by_incident.values():
        if len(proposals) <= 1:
            continue
        actions = sorted(item["report"]["action"] for item in proposals)
        for item in proposals:
            report = item["report"]
            report["blockers"].append({
                "code": "competing_incident_routes",
                "message": "Multiple proposed routes for this incident need one owner decision: "
                           + ", ".join(actions) + ".",
            })
            if "incident_route_selection" not in report["required_evidence"]:
                report["required_evidence"].append("incident_route_selection")
            report["decision"] = "HOLD"

    workorders = _batch_workorders(results)
    # The incident desk is source-bound to this exact batch. It is a
    # human worklist, not a live queue and not permission to claim work.
    incident_triage = []
    for (project_id, incident_id), proposals in by_incident.items():
        reports = [item["report"] for item in proposals]
        owners = {report["owner"] for report in reports}
        shared_owner = next(iter(owners)) if len(owners) == 1 else None
        holds = sorted({blocker["code"] for report in reports
                        for blocker in report["blockers"]})
        requested = sorted({field for report in reports
                            for field in report["required_evidence"]})
        if len(reports) > 1:
            next_step = "choose_one_incident_route_with_authorized_owner"
        elif holds:
            next_step = "resolve_observed_holds_and_refresh_source"
        else:
            next_step = "independent_owner_gate_review_required"
        incident_triage.append({
            "project_id": project_id,
            "incident_id": incident_id,
            "proposed_actions": sorted(report["action"] for report in reports),
            "request_indices": sorted(item["index"] for item in proposals),
            "decision": "HOLD" if holds else "READY_FOR_HUMAN_REVIEW",
            "blocker_codes": holds,
            "required_evidence": requested,
            "operator_owner_hint": shared_owner,
            "owner_hint_independently_verified": False,
            "owner_hints_conflict": len(owners) > 1,
            "source_urls": sorted({report["source_url"] for report in reports}),
            "next_step": next_step,
            "claim_authorized": False,
            "merge_authorized": False,
            "deploy_authorized": False,
        })
    incident_triage.sort(key=lambda row: (
        row["decision"] != "HOLD", row["project_id"], row["incident_id"]
    ))

    decisions = [item["report"]["decision"] for item in results]
    return {
        "schema_version": 1,
        "policy": "zsentinel-recovery-policy-batch-v1",
        "evaluated_at_utc": evaluated_at.isoformat().replace("+00:00", "Z"),
        "request_count": len(results),
        "incident_count": len(workorders),
        "owner_workorders": workorders,
        "decision_counts": {
            "HOLD": decisions.count("HOLD"),
            "READY_FOR_HUMAN_REVIEW": decisions.count("READY_FOR_HUMAN_REVIEW"),
        },
        "results": results,
        "incident_count": len(incident_triage),
        "incident_triage": incident_triage,
        "advisory_only": True,
        "evidence_independently_verified": False,
        "actions_executed": False,
        "authority": {
            "claim_acquired": False,
            "ci_retried": False,
            "merge_executed": False,
            "deployment_executed": False,
            "runner_restarted": False,
        },
    }


def markdown(report):
    lines = [
        "# zSentinel recovery policy decision",
        "",
        "- Project: " + report["project_id"],
        "- Incident: " + report["incident_id"],
        "- Proposed action: " + report["action"],
        "- Decision: **" + report["decision"] + "**",
        "- Source observation: " + report["observed_at_utc"],
        "- Source: <code>" + html.escape(report["source_url"], quote=True) + "</code>",
        "- Evidence independently verified: no",
        "- Action executed: no",
        "",
    ]
    if report["blockers"]:
        lines.extend(["## Holds to resolve", ""])
        lines.extend("- " + item["code"] + " — " + item["message"]
                     for item in report["blockers"])
    else:
        lines.extend([
            "## Remaining release boundary",
            "",
            "The supplied fields meet this local policy profile. This is not approval or execution; "
            "the real owner, GitHub exact-head checks, branch protection and runtime release gates remain authoritative.",
        ])
    if report["required_evidence"]:
        lines.extend(["", "## Required evidence", ""])
        lines.extend("- " + field for field in report["required_evidence"])
    lines.extend([
        "",
        "## Side-effect boundary",
        "",
        "No claim, retry, merge, deployment or runner operation was performed.",
        "",
    ])
    return "\n".join(lines)


def markdown_batch(batch):
    lines = [
        "# zSentinel recovery policy batch",
        "",
        "- Evaluated at: " + batch["evaluated_at_utc"],
        "- Requests: " + str(batch["request_count"]),
        "- HOLD: " + str(batch["decision_counts"]["HOLD"]),
        "- Ready for human review: " + str(batch["decision_counts"]["READY_FOR_HUMAN_REVIEW"]),
        "- Evidence independently verified: no",
        "- Actions executed: no",
        "",
        "## Incident-owner workorders (advisory)",
        "",
        workorders_text(batch),
        "",
        "## Per-proposal evidence",
        "",
    ]
    for item in batch["results"]:
        report = item["report"]
        lines.extend([
            "## " + str(item["index"] + 1) + ". " + report["project_id"] + " / " + report["incident_id"],
            "",
            "- Proposed action: **" + report["action"] + "**",
            "- Decision: **" + report["decision"] + "**",
            "- Source observation: " + report["observed_at_utc"],
            "- Source: <code>" + html.escape(report["source_url"], quote=True) + "</code>",
            "- Source age: " + str(report["source_age_seconds"]) + " seconds",
            "- Evidence independently verified: no",
            "- Action executed: no",
            "",
        ])
        if report["blockers"]:
            lines.extend(["### Holds to resolve", ""])
            lines.extend(
                "- " + entry["code"] + " — " + entry["message"]
                for entry in report["blockers"]
            )
        else:
            lines.extend([
                "### Remaining boundary",
                "",
                "The supplied fields meet the local policy profile; this is not approval or execution.",
            ])
        if report["required_evidence"]:
            lines.extend(["", "### Required evidence", ""])
            lines.extend("- " + field for field in report["required_evidence"])
        lines.append("")
    lines.extend([
        "## Side-effect boundary",
        "",
        "The batch is advisory only. No claim, retry, merge, deployment or runner operation was performed.",
        "",
    ])
    return "\n".join(lines)


def template_request(action, project_id, incident_id, source_url, owner):
    if type(action) is not str or action not in ACTIONS:
        raise PolicyInputError("action must be one of: " + ", ".join(sorted(ACTIONS)))
    if type(project_id) is not str or not SLUG.fullmatch(project_id):
        raise PolicyInputError("project_id must be a bounded ASCII identifier")
    if type(incident_id) is not str or not SLUG.fullmatch(incident_id):
        raise PolicyInputError("incident_id must be a bounded ASCII identifier")
    source = _source_url(source_url)
    if owner is not None and (type(owner) is not str or not SLUG.fullmatch(owner)):
        raise PolicyInputError("owner must be null or a bounded ASCII identifier")
    evidence = {field: None for field in sorted(EVIDENCE_FIELDS)}
    return {
        "schema_version": 1,
        "action": action,
        "project_id": project_id,
        "incident_id": incident_id,
        "observed_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "source_url": source,
        "owner": owner,
        "evidence": evidence,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Explain whether a proposed recovery action is ready for human review; never execute it."
    )
    parser.add_argument("--input", help="one bounded JSON request file path, or - for stdin")
    parser.add_argument("--batch-input", help="bounded JSON array of 1–25 independent requests, file path or - for stdin")
    parser.add_argument("--template", choices=sorted(ACTIONS),
                        help="emit a fresh, fail-closed JSON request template")
    parser.add_argument("--project-id", help="project slug used by --template")
    parser.add_argument("--incident-id", help="incident slug used by --template")
    parser.add_argument("--source-url", help="canonical HTTPS GitHub/Notion source used by --template")
    parser.add_argument("--owner", help="known owner slug used by --template; omit if unknown")
    parser.add_argument("--format", choices=("json", "markdown", "workorders"), default="json")
    parser.add_argument("--fail-on-hold", action="store_true", help="return exit code 2 when a valid evaluation contains any HOLD")
    parser.add_argument("--max-age-minutes", type=int, default=60,
                        help="maximum accepted observation age (1..1440; default 60)")
    args = parser.parse_args(argv)
    if not 1 <= args.max_age_minutes <= 1440:
        parser.error("--max-age-minutes must be between 1 and 1440")
    if args.template:
        if args.input or args.batch_input:
            parser.error("--template cannot be used with --input or --batch-input")
        if not all((args.project_id, args.incident_id, args.source_url)):
            parser.error("--template requires --project-id, --incident-id and --source-url")
        if args.format != "json":
            parser.error("--template emits JSON only")
        if args.fail_on_hold:
            parser.error("--fail-on-hold requires --input or --batch-input")
        try:
            report = template_request(args.template, args.project_id, args.incident_id,
                                      args.source_url, args.owner)
            output = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            if len(output.encode("utf-8")) > MAX_OUTPUT_BYTES:
                raise PolicyInputError("template exceeds 128 KiB")
            sys.stdout.write(output)
            return 0
        except (OSError, UnicodeError, PolicyInputError) as exc:
            print("zSentinel recovery policy: " + str(exc), file=sys.stderr)
            return 2
    if args.batch_input:
        if args.input:
            parser.error("--input and --batch-input cannot be used together")
        if any((args.project_id, args.incident_id, args.source_url, args.owner)):
            parser.error("template identity options require --template")
        try:
            raw = _read_input(args.batch_input)
            data = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                              parse_constant=_reject_constant)
            report = evaluate_batch(data, args.max_age_minutes)
            output = (workorders_text(report) if args.format == "workorders" else
                      markdown_batch(report) if args.format == "markdown" else
                      json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
            encoded = output.encode("utf-8")
            if len(encoded) > MAX_OUTPUT_BYTES:
                raise PolicyInputError("output exceeds 128 KiB")
            sys.stdout.write(output)
            return 2 if args.fail_on_hold and report["decision_counts"]["HOLD"] > 0 else 0
        except (OSError, UnicodeError, json.JSONDecodeError, PolicyInputError) as exc:
            print("zSentinel recovery policy: " + str(exc), file=sys.stderr)
            return 2
    if not args.input:
        parser.error("either --input, --batch-input or --template is required")
    if args.format == "workorders":
        parser.error("--format workorders requires --batch-input")
    if any((args.project_id, args.incident_id, args.source_url, args.owner)):
        parser.error("template identity options require --template")
    try:
        raw = _read_input(args.input)
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                          parse_constant=_reject_constant)
        report = evaluate(data, args.max_age_minutes)
        output = markdown(report) if args.format == "markdown" else (
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        )
        encoded = output.encode("utf-8")
        if len(encoded) > MAX_OUTPUT_BYTES:
            raise PolicyInputError("output exceeds 128 KiB")
        sys.stdout.write(output)
        return 2 if args.fail_on_hold and report["decision"] == "HOLD" else 0
    except (OSError, UnicodeError, json.JSONDecodeError, PolicyInputError) as exc:
        print("zSentinel recovery policy: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

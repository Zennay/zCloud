#!/usr/bin/env python3
"""Explain whether one proposed recovery action is ready for human review.

This is a local, advisory policy evaluator. It does not read live services,
contact GitHub/Notion, acquire claims, retry CI, merge PRs, deploy or restart
runners. Operator-supplied evidence is never treated as independently verified.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

MAX_INPUT_BYTES = 256 * 1024
MAX_OUTPUT_BYTES = 128 * 1024
UTC = timezone.utc
SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
ACTIONS = frozenset({
    "retry_ci", "route_root_fix", "assign_independent_work",
    "merge_pr", "deploy", "restart_runner",
})
ROOT_FIELDS = frozenset({
    "schema_version", "action", "project_id", "incident_id",
    "observed_at_utc", "source_url", "owner", "evidence",
})
EVIDENCE_FIELDS = frozenset({
    "source_verified", "failure_kind", "attempts", "retry_budget",
    "backoff_elapsed", "active_claim", "independent_task_ready",
    "scope_disjoint", "capacity", "exact_head", "required_ci",
    "independent_review", "branch_protection", "owner_authorized",
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
}
BOOL_FIELDS = {
    "source_verified", "backoff_elapsed", "independent_task_ready",
    "scope_disjoint", "owner_authorized", "root_cause_confirmed",
    "release_approval", "runtime_owner_authorized", "human_approval",
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
    return value


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


def evaluate(data, max_age_minutes):
    observed, source, evidence = _validate(data)
    now = datetime.now(UTC)
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


def markdown(report):
    lines = [
        "# zSentinel recovery policy decision",
        "",
        "- Project: " + report["project_id"],
        "- Incident: " + report["incident_id"],
        "- Proposed action: " + report["action"],
        "- Decision: **" + report["decision"] + "**",
        "- Source observation: " + report["observed_at_utc"],
        "- Source: " + report["source_url"],
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


def template_request(action, project_id, incident_id, source_url, owner):
    if type(project_id) is not str or not SLUG.fullmatch(project_id):
        raise PolicyInputError("project_id must be a bounded ASCII identifier")
    if type(incident_id) is not str or not SLUG.fullmatch(incident_id):
        raise PolicyInputError("incident_id must be a bounded ASCII identifier")
    _source_url(source_url)
    if owner is not None and (type(owner) is not str or not SLUG.fullmatch(owner)):
        raise PolicyInputError("owner must be null or a bounded ASCII identifier")
    evidence = {field: None for field in sorted(EVIDENCE_FIELDS)}
    return {
        "schema_version": 1,
        "action": action,
        "project_id": project_id,
        "incident_id": incident_id,
        "observed_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "source_url": source_url,
        "owner": owner,
        "evidence": evidence,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Explain whether a proposed recovery action is ready for human review; never execute it."
    )
    parser.add_argument("--input", help="bounded JSON file path, or - for stdin")
    parser.add_argument("--template", choices=sorted(ACTIONS),
                        help="emit a fresh, fail-closed JSON request template")
    parser.add_argument("--project-id", help="project slug used by --template")
    parser.add_argument("--incident-id", help="incident slug used by --template")
    parser.add_argument("--source-url", help="canonical HTTPS GitHub/Notion source used by --template")
    parser.add_argument("--owner", help="known owner slug used by --template; omit if unknown")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--max-age-minutes", type=int, default=60,
                        help="maximum accepted observation age (1..1440; default 60)")
    args = parser.parse_args(argv)
    if not 1 <= args.max_age_minutes <= 1440:
        parser.error("--max-age-minutes must be between 1 and 1440")
    if args.template:
        if args.input:
            parser.error("--template and --input cannot be used together")
        if not all((args.project_id, args.incident_id, args.source_url)):
            parser.error("--template requires --project-id, --incident-id and --source-url")
        if args.format != "json":
            parser.error("--template emits JSON only")
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
    if not args.input:
        parser.error("either --input or --template is required")
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
        return 0
    except (OSError, UnicodeError, json.JSONDecodeError, PolicyInputError) as exc:
        print("zSentinel recovery policy: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

    parser = argparse.ArgumentParser(
        description="Explain whether a proposed recovery action is ready for human review; never execute it."
    )
    parser.add_argument("--input", required=True, help="bounded JSON file path, or - for stdin")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--max-age-minutes", type=int, default=60,
                        help="maximum accepted observation age (1..1440; default 60)")
    args = parser.parse_args(argv)
    if not 1 <= args.max_age_minutes <= 1440:
        parser.error("--max-age-minutes must be between 1 and 1440")
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
        return 0
    except (OSError, UnicodeError, json.JSONDecodeError, PolicyInputError) as exc:
        print("zSentinel recovery policy: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

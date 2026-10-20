#!/usr/bin/env python3
"""zSentinel bounded current exact-head PR gate desk, read-only.

Current checks and submitted reviews are OBSERVATIONS, never GitHub's
full required-check rule set, branch protection, release or merge permission.
"""
from __future__ import annotations

from datetime import datetime, timezone
import re

from zsentinel_pr_scope_guard import github_json, REPO, SHA

SCHEMA = "zsentinel-current-pr-gate-observation-v1"
MAX_PRS = 4
NUMBER = re.compile(r"^[1-9][0-9]{0,8}$")
OK_CONCLUSIONS = frozenset({"success", "neutral", "skipped"})


def _sha(value):
    if type(value) is not str or not SHA.fullmatch(value):
        raise ValueError("missing exact commit SHA")
    return value


def _one(repo, number, token):
    if not REPO.fullmatch(repo) or type(number) is not int or not 1 <= number <= 999999999:
        raise ValueError("invalid canonical PR identity")
    prefix = "/pulls/" + str(number)
    first = github_json(repo, prefix, token)
    if (type(first) is not dict or first.get("number") != number
            or first.get("state") != "open" or type(first.get("base")) is not dict
            or type(first.get("head")) is not dict):
        raise ValueError("PR not open or source missing")
    head = _sha(first["head"].get("sha"))
    base = _sha(first["base"].get("sha"))
    base_repo = first["base"].get("repo")
    author = first.get("user")
    if (type(base_repo) is not dict or base_repo.get("full_name") != repo
            or type(author) is not dict or type(author.get("login")) is not str):
        raise ValueError("PR not based in canonical repository")
    checks = github_json(repo, "/commits/" + head + "/check-runs?per_page=100", token)
    if (type(checks) is not dict or type(checks.get("total_count")) is not int
            or not 0 <= checks["total_count"] <= 1000000
            or type(checks.get("check_runs")) is not list
            or len(checks["check_runs"]) > 100):
        raise ValueError("bounded check-runs source unavailable")
    check_rows = checks["check_runs"]
    statuses = {"completed_observed": 0, "pending_observed": 0,
                "failure_observed": 0, "other_observed": 0}
    seen_check_ids = set()
    for row in check_rows:
        if type(row) is not dict or type(row.get("id")) is not int or row["id"] in seen_check_ids:
            raise ValueError("invalid or repeated check source identity")
        seen_check_ids.add(row["id"])
        if row.get("head_sha") not in (None, head):
            raise ValueError("check source head drift")
        state, conclusion = row.get("status"), row.get("conclusion")
        if state == "completed":
            statuses["completed_observed"] += 1
            if conclusion not in OK_CONCLUSIONS:
                statuses["failure_observed"] += 1
        elif state in ("queued", "in_progress", "pending", "waiting", "requested"):
            statuses["pending_observed"] += 1
        else:
            statuses["other_observed"] += 1
    # Check Runs and legacy commit statuses are distinct GitHub evidence
    # surfaces. A successful Check Run is not proof that a required legacy
    # commit-status context succeeded. Keep both visible and non-authorizing.
    legacy = github_json(repo, "/commits/" + head + "/status?per_page=100", token)
    if (type(legacy) is not dict or legacy.get("sha") != head
            or type(legacy.get("statuses")) is not list
            or len(legacy["statuses"]) > 100):
        raise ValueError("legacy commit statuses missing or head changed")
    latest_context = {}
    seen_status_ids = set()
    for row in legacy["statuses"]:
        if (type(row) is not dict or type(row.get("id")) is not int
                or row["id"] in seen_status_ids
                or type(row.get("context")) is not str
                or not 1 <= len(row["context"]) <= 200
                or row.get("state") not in ("success", "pending", "failure", "error")):
            raise ValueError("invalid legacy status receipt")
        seen_status_ids.add(row["id"])
        # GitHub may retain earlier failed statuses for a now-green context.
        # Use the highest status ID observed for each exact context rather
        # than reporting superseded failures as active, but never claim that
        # the sampled set enumerates all branch-protection requirements.
        name = row["context"]
        if name not in latest_context or row["id"] > latest_context[name]["id"]:
            latest_context[name] = row
    legacy_states = {"success": 0, "pending": 0, "failure": 0, "error": 0}
    for row in latest_context.values():
        legacy_states[row["state"]] += 1
    legacy_partial = len(legacy["statuses"]) == 100
    reviews = github_json(repo, prefix + "/reviews?per_page=100", token)
    if type(reviews) is not list or len(reviews) > 100:
        raise ValueError("bounded reviews source unavailable")
    most_recent = {}
    for row in reviews:
        if (type(row) is not dict or type(row.get("id")) is not int
                or type(row.get("user")) is not dict):
            raise ValueError("invalid review receipt")
        reviewer = row["user"].get("login")
        if type(reviewer) is not str or not 1 <= len(reviewer) <= 70:
            raise ValueError("untrusted reviewer identity")
        # Only reviews explicitly submitted for the current exact commit
        # can count as fresh positive observations. Dismissals supersede.
        if row.get("commit_id") != head:
            continue
        if reviewer == author["login"]:
            continue
        review_state = row.get("state")
        if review_state not in ("APPROVED", "CHANGES_REQUESTED", "COMMENTED", "DISMISSED"):
            continue
        current = most_recent.get(reviewer)
        if current is None or row["id"] > current[0]:
            most_recent[reviewer] = (row["id"], review_state)
    last = github_json(repo, prefix, token)
    if (type(last) is not dict or last.get("number") != number
            or last.get("state") != "open"
            or type(last.get("head")) is not dict
            or type(last.get("base")) is not dict
            or last["head"].get("sha") != head
            or last["base"].get("sha") != base):
        raise ValueError("PR head/base changed during observation")
    approved = sum(state == "APPROVED" for _, state in most_recent.values())
    requested = sum(state == "CHANGES_REQUESTED" for _, state in most_recent.values())
    check_complete = (len(check_rows) == checks["total_count"] and
                      statuses["pending_observed"] == 0 and
                      statuses["other_observed"] == 0)
    return {
        "url": "https://github.com/" + repo + "/pull/" + str(number),
        "repository": repo, "number": number,
        "head_sha_observed": head, "base_sha_observed": base,
        "draft_at_read": last.get("draft") is True,
        "github_mergeable_boolean_observed": last.get("mergeable"),
        "check_runs_total_observed": checks["total_count"],
        "check_runs_page_complete": len(check_rows) == checks["total_count"],
        "check_runs_sample_complete_and_terminal": check_complete,
        "observed_checks_completed": statuses["completed_observed"],
        "observed_checks_pending": statuses["pending_observed"],
        "observed_checks_not_successful": statuses["failure_observed"],
        "observed_checks_unknown": statuses["other_observed"],
        "legacy_status_contexts_observed": len(latest_context),
        "legacy_status_success_observed": legacy_states["success"],
        "legacy_status_pending_observed": legacy_states["pending"],
        "legacy_status_failure_or_error_observed": (
            legacy_states["failure"] + legacy_states["error"]),
        "legacy_status_source_may_be_truncated": legacy_partial,
        "legacy_status_and_checks_required_set_verified": False,
        "observed_independent_exact_head_approvals": approved,
        "observed_exact_head_changes_requested": requested,
        "review_page_may_be_truncated": len(reviews) == 100,
        "exact_head_stable_on_second_read": True,
        "required_branch_checks_and_rules_verified": False,
        "required_review_policy_verified": False,
        "mergable_by_automation": False,
        "merge_authorized": False,
        "deployment_authorized": False,
        "read_only": True,
        "status": "observed_current_head_partial_gate_evidence",
    }


def observe_many(prs, token=None):
    if (type(prs) is not list or not 1 <= len(prs) <= MAX_PRS
            or len({tuple(p) for p in prs if type(p) in (tuple, list) and len(p) == 2})
                   != len(prs)):
        raise ValueError("choose one to four distinct canonical existing PRs")
    items = []
    for entry in prs:
        if (type(entry) not in (tuple, list) or len(entry) != 2
                or type(entry[0]) is not str or not REPO.fullmatch(entry[0])
                or type(entry[1]) is not int or not 1 <= entry[1] <= 999999999):
            raise ValueError("invalid PR identity")
        repo, number = entry
        try:
            result = _one(repo, number, token)
        except (OSError, ValueError):
            result = {
                "url": "https://github.com/" + repo + "/pull/" + str(number),
                "repository": repo, "number": number,
                "status": "github_source_unavailable_or_head_changed",
                "required_branch_checks_and_rules_verified": False,
                "required_review_policy_verified": False,
                "merge_authorized": False, "deployment_authorized": False,
                "read_only": True,
            }
        items.append(result)
    return {
        "schema": SCHEMA,
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "items": items,
        "observed_prs": len(items),
        "max_github_gets": len(items) * 5,
        "current_review_and_check_samples_only": True,
        "required_branch_protection_verified": False,
        "merge_authorized": False, "deployment_authorized": False,
        "writes_performed": False,
    }

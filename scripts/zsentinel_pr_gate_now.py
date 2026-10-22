#!/usr/bin/env python3
"""zSentinel bounded current exact-head PR gate desk, read-only.

Current checks and submitted reviews are OBSERVATIONS, never GitHub's
full required-check rule set, branch protection, release or merge permission.
"""
from __future__ import annotations

import argparse
import hashlib
from datetime import datetime, timezone
from html import escape
import json
import os
from pathlib import Path
import re
import stat
import sys

from zsentinel_pr_scope_guard import github_json, REPO, SHA
from zsentinel_private_report_writer import write_private_report

SCHEMA = "zsentinel-current-pr-gate-observation-v1"
# Shared Dutch owner actions for direct receipts, live issue UI and saved batches.
# They explain human decisions; none of them grants merge/claim authority.
GATE_STEPS_NL = {
    "owner_finish_draft_before_merge_review":
        "Concept-PR: bevoegde eigenaar rondt product af en maakt reviewklaar.",
    "owner_resolve_current_base_conflicts":
        "GitHub meldt mergeconflict op huidige basis; bevoegde PR-eigenaar lost dit op.",
    "wait_for_github_mergeability_evaluation":
        "GitHub moet samenvoegbaarheid nog berekenen; nieuwe actuele bron nodig.",
    "inspect_incomplete_current_head_gate_source":
        "Onvolledige GitHub check-/reviewbron; bewijs opnieuw ophalen.",
    "inspect_current_head_failed_checks_with_owner":
        "Mislukte huidige checks; bevoegde PR-eigenaar onderzoekt de oorzaak.",
    "wait_for_existing_current_head_checks":
        "Bestaande exact-head checks lopen nog; niet opnieuw starten.",
    "owner_triage_observed_slow_current_head_checks":
        "Onderzoek de getoonde langdurige queued/lopende checks en hun runner-/toewijzingsstatus "
        "met de bevoegde eigenaar; geen automatische herstart of CI-retry.",
    "owner_address_exact_head_review_changes":
        "Onafhankelijke reviewer heeft wijzigingen gevraagd; eigenaar handelt die af.",
    "verify_missing_current_head_checks_and_rules":
        "Geen checks gezien; verplichte checks en branchregels apart controleren.",
    "wait_for_requested_independent_review":
        "Onafhankelijke review is al aangevraagd bij persoon/team; wacht op beslissing.",
    "request_independent_current_head_review":
        "Vraag onafhankelijke review op de actuele HEAD aan; bestaande CI mag parallel doorlopen.",
    "verify_required_rules_ownership_and_release_separately":
        "Branchregels, eigenaar en release apart verifiëren; nog geen mergevrijgave.",
}
MAX_PRS = 4
NUMBER = re.compile(r"^[1-9][0-9]{0,8}$")
# GitHub App actors have logins like dependabot[bot]. They may open PRs
# and leave reviews; neither their casing nor their automation identity
# is evidence of an independent human approval.
GITHUB_ACTOR = re.compile(r"^[A-Za-z0-9-]{1,39}(?:\[bot\])?$")
OK_CONCLUSIONS = frozenset({"success", "neutral", "skipped"})
# Owner attention only; this is not a required CI gate or proof of runner failure.
QUEUED_CHECK_ATTENTION_AFTER_SECONDS = 30 * 60
# Read-only warning threshold, not evidence of a frozen process or a right
# to restart/reassign a runner. Runtime may legitimately exceed one hour.
RUNNING_CHECK_ATTENTION_AFTER_SECONDS = 60 * 60


def _sha(value):
    if type(value) is not str or not SHA.fullmatch(value):
        raise ValueError("missing exact commit SHA")
    return value


def _branch_ref(value):
    """Validate GitHub's short target branch name before pinning or rendering it."""
    forbidden = (" ", "~", "^", ":", "?", "*", "[", "\\")
    if (type(value) is not str or not 1 <= len(value) <= 255
            or value != value.strip() or value.startswith("/")
            or value.endswith("/") or "//" in value or ".." in value
            or "@{" in value or any(ord(char) < 32 or ord(char) == 127
                                    or char in forbidden for char in value)
            or any(not part or part.startswith(".") or part.endswith(".")
                   or part.lower().endswith(".lock") for part in value.split("/"))):
        raise ValueError("invalid target branch identity")
    return value


def _source_evidence_fingerprint(source_kind, observed_rows):
    """Deterministic bounded evidence identity; never approval or authentication."""
    if source_kind not in ("check_runs", "legacy_status", "current_head_reviews",
                           "pending_review_requests"):
        raise ValueError("unrecognized read-only evidence category")
    payload = json.dumps(
        ["zsentinel-gate-evidence-v1", source_kind, observed_rows],
        ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _pending_review_requests(pr):
    """Pin a bounded set of pending review requests without inferring approvals."""
    people, teams = pr.get("requested_reviewers"), pr.get("requested_teams")
    if (type(people) is not list or type(teams) is not list
            or len(people) > 100 or len(teams) > 100):
        raise ValueError("requested review state unavailable or unbounded")
    users, groups = [], []
    for entry in people:
        login = entry.get("login") if type(entry) is dict else None
        if (type(login) is not str or
                re.fullmatch(r"[A-Za-z0-9-]{1,39}", login) is None):
            raise ValueError("invalid requested individual review identity")
        users.append(login.lower())
    for entry in teams:
        slug = entry.get("slug") if type(entry) is dict else None
        org = entry.get("organization") if type(entry) is dict else None
        # Team requests are counted, not treated as individual approvals.
        if (type(slug) is not str or
                re.fullmatch(r"[A-Za-z0-9_-]{1,100}", slug) is None):
            raise ValueError("invalid requested team review identity")
        groups.append(slug.lower())
    if len(set(users)) != len(users) or len(set(groups)) != len(groups):
        raise ValueError("repeated pending review request")
    return sorted(users), sorted(groups)


def _queued_age_from_github(check, now):
    """Bounded read-only elapsed age; missing/future timestamps are unknown."""
    if check.get("status") != "queued":
        return None
    raw = check.get("created_at")
    if type(raw) is not str or len(raw) > 40:
        return None
    try:
        timestamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if timestamp.tzinfo is None:
        return None
    seconds = (now - timestamp.astimezone(timezone.utc)).total_seconds()
    if seconds < 0:
        return None
    return min(int(seconds), 365 * 24 * 3600)


def _running_age_from_github(check, now):
    """Age of a GitHub-reported in-progress check, never runner-health proof."""
    if check.get("status") != "in_progress":
        return None
    raw = check.get("started_at")
    if type(raw) is not str or len(raw) > 40:
        return None
    try:
        timestamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if timestamp.tzinfo is None:
        return None
    seconds = (now - timestamp.astimezone(timezone.utc)).total_seconds()
    if seconds < 0:
        return None
    return min(int(seconds), 365 * 24 * 3600)


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
    base_ref = _branch_ref(first["base"].get("ref"))
    requested_people, requested_teams = _pending_review_requests(first)
    base_repo = first["base"].get("repo")
    author = first.get("user")
    if (type(base_repo) is not dict or base_repo.get("full_name") != repo
            or type(author) is not dict or type(author.get("login")) is not str
            or GITHUB_ACTOR.fullmatch(author["login"]) is None):
        raise ValueError("PR not based in canonical repository")
    author_identity = author["login"].casefold()
    checks = github_json(repo, "/commits/" + head + "/check-runs?per_page=100", token)
    if (type(checks) is not dict or type(checks.get("total_count")) is not int
            or not 0 <= checks["total_count"] <= 1000000
            or type(checks.get("check_runs")) is not list
            or len(checks["check_runs"]) > 100):
        raise ValueError("bounded check-runs source unavailable")
    check_rows = checks["check_runs"]
    # GitHub retains several Check Run attempts for the same name/app on a
    # commit. Only the newest observed attempt is actionable. An older failed
    # attempt MUST NOT continue blocking the operator after a newer success.
    # This is still a sampled observation, never required-check verification.
    statuses = {"completed_observed": 0, "pending_observed": 0,
                "failure_observed": 0, "other_observed": 0}
    seen_check_ids = set()
    latest_checks = {}
    for row in check_rows:
        if (type(row) is not dict or type(row.get("id")) is not int
                or row["id"] <= 0 or row["id"] in seen_check_ids):
            raise ValueError("invalid or repeated check source identity")
        seen_check_ids.add(row["id"])
        if row.get("head_sha") not in (None, head):
            raise ValueError("check source head drift")
        app = row.get("app")
        name = row.get("name")
        if (type(app) is not dict or type(app.get("id")) is not int
                or app["id"] <= 0 or type(name) is not str
                or not 1 <= len(name) <= 180
                or any(ord(char) < 32 or ord(char) == 127 for char in name)):
            raise ValueError("check run identity incomplete")
        key = (app["id"], name)
        if key not in latest_checks or row["id"] > latest_checks[key]["id"]:
            latest_checks[key] = row
    # Retain source identity even if each count looks identical: a rerun or
    # new success can supersede a prior check without altering aggregate counts.
    check_fingerprint = _source_evidence_fingerprint("check_runs", sorted(
        ({"app_id": app_id, "name": name,
          "check_run_id": row["id"], "status": row.get("status"),
          "conclusion": row.get("conclusion")}
         for (app_id, name), row in latest_checks.items()),
        key=lambda item: (item["app_id"], item["name"])))
    actionable = []
    observed_queue_time = datetime.now(timezone.utc)
    queued_over_attention = 0
    queued_age_unknown = 0
    running_over_attention = 0
    running_age_unknown = 0
    for row in latest_checks.values():
        state, conclusion = row.get("status"), row.get("conclusion")
        if state == "completed":
            statuses["completed_observed"] += 1
            if conclusion not in OK_CONCLUSIONS:
                statuses["failure_observed"] += 1
        elif state in ("queued", "in_progress", "pending", "waiting", "requested"):
            statuses["pending_observed"] += 1
        else:
            statuses["other_observed"] += 1
        age = _queued_age_from_github(row, observed_queue_time)
        running_age = _running_age_from_github(row, observed_queue_time)
        if state == "in_progress":
            if running_age is None:
                running_age_unknown += 1
            elif running_age >= RUNNING_CHECK_ATTENTION_AFTER_SECONDS:
                running_over_attention += 1
        if state == "queued":
            if age is None:
                queued_age_unknown += 1
            elif age >= QUEUED_CHECK_ATTENTION_AFTER_SECONDS:
                queued_over_attention += 1
        if ((state == "completed" and conclusion not in OK_CONCLUSIONS)
                or state != "completed"):
            actionable.append({
                "name": row["name"],
                "check_run_id": row["id"],
                # Canonical link built from trusted repository + numeric ID;
                # never follow or publish arbitrary provider details_url.
                "url": "https://github.com/" + repo + "/runs/" + str(row["id"]),
                "state": state if type(state) is str and state in
                    ("completed", "queued", "in_progress", "pending",
                     "waiting", "requested") else "unknown",
                "conclusion": conclusion if type(conclusion) is str and
                    len(conclusion) <= 40 else None,
                # Observable GitHub age is not proof the runner is offline.
                "queued_age_seconds_observed": age,
                "queued_attention_30m": (
                    age is not None and
                    age >= QUEUED_CHECK_ATTENTION_AFTER_SECONDS),
                "running_age_seconds_observed": running_age,
                "running_attention_60m": (
                    running_age is not None and
                    running_age >= RUNNING_CHECK_ATTENTION_AFTER_SECONDS),
            })
    # Put actionable *observed* long-running checks ahead of routine queued
    # checks within the bounded 12-row operator sample, without claiming a
    # failure, runner outage, or authority to dispatch/retry.
    actionable.sort(key=lambda item: (
        0 if item["state"] == "completed" else
        1 if item["running_attention_60m"] else
        2 if item["queued_attention_30m"] else 3,
        item["name"], item["check_run_id"]))
    actionable_hidden = max(0, len(actionable) - 12)
    actionable = actionable[:12]
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
    legacy_fingerprint = _source_evidence_fingerprint("legacy_status", sorted(
        ({"context": name, "status_id": row["id"], "state": row["state"]}
         for name, row in latest_context.items()),
        key=lambda item: item["context"]))
    legacy_states = {"success": 0, "pending": 0, "failure": 0, "error": 0}
    for row in latest_context.values():
        legacy_states[row["state"]] += 1
    legacy_partial = len(legacy["statuses"]) == 100
    # Show the latest failing/pending legacy contexts as actionable source,
    # without trusting unbounded provider target_url or exposing credentials.
    legacy_actionable_all = [
        {"context": row["context"], "state": row["state"],
         "url": "https://github.com/" + repo + "/commit/" + head + "/checks"}
        for row in latest_context.values() if row["state"] != "success"
    ]
    legacy_actionable_all.sort(key=lambda row: (
        row["state"] not in ("failure", "error"), row["context"]))
    legacy_actionable_hidden = max(0, len(legacy_actionable_all) - 12)
    legacy_actionable = legacy_actionable_all[:12]
    reviews = github_json(repo, prefix + "/reviews?per_page=100", token)
    if type(reviews) is not list or len(reviews) > 100:
        raise ValueError("bounded reviews source unavailable")
    most_recent = {}
    automation_reviews_ignored = 0
    for row in reviews:
        if (type(row) is not dict or type(row.get("id")) is not int
                or type(row.get("user")) is not dict):
            raise ValueError("invalid review receipt")
        reviewer = row["user"].get("login")
        if type(reviewer) is not str or GITHUB_ACTOR.fullmatch(reviewer) is None:
            raise ValueError("untrusted reviewer identity")
        # GitHub logins are case-insensitive. Never treat differently cased
        # aliases as distinct approvals, or an author's case variant as an
        # independent reviewer on the exact current commit.
        reviewer_identity = reviewer.casefold()
        # Only reviews explicitly submitted for the current exact commit
        # can count as fresh positive observations. Dismissals supersede.
        if row.get("commit_id") != head:
            continue
        if reviewer_identity == author_identity:
            continue
        # A GitHub App/bot review can be useful activity context but is not
        # the separate independent human review required by release policy.
        # Count it explicitly rather than making the entire PR unreadable.
        if reviewer_identity.endswith("[bot]") or row["user"].get("type") == "Bot":
            automation_reviews_ignored += 1
            continue
        review_state = row.get("state")
        # COMMENTED is not a review decision. A later ordinary comment must
        # never silently replace an earlier exact-HEAD CHANGES_REQUESTED or
        # APPROVED decision by the same independent reviewer.
        if review_state not in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
            continue
        current = most_recent.get(reviewer_identity)
        if current is None or row["id"] > current[0]:
            most_recent[reviewer_identity] = (row["id"], review_state)
    review_fingerprint = _source_evidence_fingerprint(
        "current_head_reviews", sorted(
            ({"reviewer": name, "review_id": entry[0], "state": entry[1]}
             for name, entry in most_recent.items()),
            key=lambda item: item["reviewer"]))
    last = github_json(repo, prefix, token)
    if (type(last) is not dict or last.get("number") != number
            or last.get("state") != "open"
            or type(last.get("head")) is not dict
            or type(last.get("base")) is not dict
            or last["head"].get("sha") != head
            or last["base"].get("sha") != base
            or _branch_ref(last["base"].get("ref")) != base_ref):
        raise ValueError("PR head/base changed during observation")
    # Draft and GitHub's mergeability signal are separate from check/review
    # receipts. Never advise an operator to merge a conflicted or draft PR
    # merely because sampled checks happen to be green.
    if (type(first.get("draft")) is not bool or type(last.get("draft")) is not bool
            or first["draft"] != last["draft"]):
        raise ValueError("PR draft state missing or changed during observation")
    if _pending_review_requests(last) != (requested_people, requested_teams):
        raise ValueError("PR review requests changed during observation")
    request_fingerprint = _source_evidence_fingerprint(
        "pending_review_requests", [{"individuals": requested_people,
                                     "teams": requested_teams}])
    mergeable = last.get("mergeable")
    if mergeable is not None and type(mergeable) is not bool:
        raise ValueError("invalid GitHub PR mergeability observation")
    approved = sum(state == "APPROVED" for _, state in most_recent.values())
    requested = sum(state == "CHANGES_REQUESTED" for _, state in most_recent.values())
    check_complete = (len(check_rows) == checks["total_count"] and
                      statuses["pending_observed"] == 0 and
                      statuses["other_observed"] == 0)
    if last["draft"]:
        gate_step = "owner_finish_draft_before_merge_review"
    elif mergeable is False:
        gate_step = "owner_resolve_current_base_conflicts"
    elif legacy_partial or len(check_rows) != checks["total_count"] or len(reviews) == 100:
        gate_step = "inspect_incomplete_current_head_gate_source"
    elif (statuses["failure_observed"] or
          legacy_states["failure"] or legacy_states["error"]):
        gate_step = "inspect_current_head_failed_checks_with_owner"
    elif requested:
        # A current-head CHANGES_REQUESTED is actionable owner work even if
        # an unrelated exact-head CI job is still queued. Do not hide it
        # behind a generic "wait for CI" message.
        gate_step = "owner_address_exact_head_review_changes"
    elif not check_rows and not latest_context:
        gate_step = "verify_missing_current_head_checks_and_rules"
    elif mergeable is None:
        gate_step = "wait_for_github_mergeability_evaluation"
    elif (running_over_attention or queued_over_attention):
        # A concrete long-wait CI source deserves owner triage now, not a
        # generic passive wait, even while a review is still outstanding.
        # Still only an observation; the owner must inspect real runner state.
        gate_step = "owner_triage_observed_slow_current_head_checks"
    elif not approved and not (requested_people or requested_teams):
        # Review can be requested while other CI is queued. This helper does
        # not submit a review or a workflow run.
        gate_step = "request_independent_current_head_review"
    elif (statuses["pending_observed"] or statuses["other_observed"] or
          legacy_states["pending"]):
        gate_step = "wait_for_existing_current_head_checks"
    elif not approved and (requested_people or requested_teams):
        gate_step = "wait_for_requested_independent_review"
    elif not approved:
        gate_step = "request_independent_current_head_review"
    else:
        gate_step = "verify_required_rules_ownership_and_release_separately"
    # Parallel owner work is separate from the primary blocked-CI action.
    # It never weakens an existing failure, CHANGES_REQUESTED or source HOLD.
    parallel_review_action = (
        "request_independent_current_head_review"
        if not last["draft"] and mergeable is True and not approved
        and not requested_people and not requested_teams
        and not requested and not statuses["failure_observed"]
        and not legacy_states["failure"] and not legacy_states["error"]
        and not legacy_partial and len(check_rows) == checks["total_count"]
        and len(reviews) < 100 and (check_rows or latest_context)
        and gate_step != "request_independent_current_head_review"
        else None)
    return {
        "url": "https://github.com/" + repo + "/pull/" + str(number),
        "repository": repo, "number": number,
        "head_sha_observed": head, "base_sha_observed": base,
        "base_ref_observed": base_ref,
        "draft_at_read": last["draft"],
        "draft_state_stable_on_second_read": True,
        "pending_review_requests_stable_on_second_read": True,
        "pending_individual_review_requests_observed": len(requested_people),
        "pending_team_review_requests_observed": len(requested_teams),
        "pending_review_request_evidence_sha256": request_fingerprint,
        "github_mergeable_boolean_observed": mergeable,
        "merge_conflict_observed": mergeable is False,
        "mergeability_not_yet_evaluated": mergeable is None,
        "check_runs_total_observed": checks["total_count"],
        "check_runs_evidence_sha256": check_fingerprint,
        "check_runs_latest_attempts_observed": len(latest_checks),
        "check_runs_superseded_attempts_observed": len(check_rows) - len(latest_checks),
        "observed_actionable_check_runs": actionable,
        "observed_actionable_check_runs_hidden": actionable_hidden,
        "check_runs_page_complete": len(check_rows) == checks["total_count"],
        "check_runs_sample_complete_and_terminal": check_complete,
        "observed_checks_completed": statuses["completed_observed"],
        "observed_checks_pending": statuses["pending_observed"],
        "observed_checks_not_successful": statuses["failure_observed"],
        "observed_checks_unknown": statuses["other_observed"],
        "queued_check_attention_30m_observed": queued_over_attention,
        "queued_check_age_unknown_observed": queued_age_unknown,
        "running_check_attention_60m_observed": running_over_attention,
        "running_check_age_unknown_observed": running_age_unknown,
        "legacy_status_contexts_observed": len(latest_context),
        "legacy_status_evidence_sha256": legacy_fingerprint,
        "legacy_status_superseded_attempts_observed":
            len(legacy["statuses"]) - len(latest_context),
        "observed_actionable_legacy_contexts": legacy_actionable,
        "observed_actionable_legacy_contexts_hidden": legacy_actionable_hidden,
        "legacy_status_success_observed": legacy_states["success"],
        "legacy_status_pending_observed": legacy_states["pending"],
        "legacy_status_failure_or_error_observed": (
            legacy_states["failure"] + legacy_states["error"]),
        "legacy_status_source_may_be_truncated": legacy_partial,
        "legacy_status_and_checks_required_set_verified": False,
        "human_next_gate_step": gate_step,
        "parallel_independent_review_step": parallel_review_action,
        "legacy_status_sample_complete_and_terminal": (
            not legacy_partial and legacy_states["pending"] == 0),
        "observed_independent_exact_head_approvals": approved,
        "automation_review_events_excluded_from_independent_approval":
            automation_reviews_ignored,
        "exact_head_review_evidence_sha256": review_fingerprint,
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


# Error categories contain no exception text, URLs, tokens, response bodies or
# user-provided descriptions. They point the operator toward a real owner
# action without claiming that an inaccessible PR was closed or merged.
SOURCE_PROBLEMS_NL = {
    "github_authentication_or_access": "GitHub-toegang ontbreekt of is geweigerd; laat bevoegde eigenaar rechten controleren.",
    "github_rate_limited": "GitHub heeft verzoeken begrensd; hervat volgens providerlimiet, zonder CI-herstart.",
    "github_remote_unavailable": "GitHub gaf een server-/netwerkfout; bron later opnieuw lezen.",
    "github_pr_or_source_missing": "PR of noodzakelijke GitHub-bron is niet gevonden; controleer bestaande referentie en toegang.",
    "pr_changed_during_observation": "PR-HEAD, doelbasis, conceptstatus of reviewaanvragen veranderden tijdens lezen; actuele bron opnieuw vastleggen.",
    "github_evidence_unavailable_or_invalid": "GitHub-bron ontbreekt, is onvolledig of onverwacht; inspecteer zonder aannames.",
}


def _source_problem_kind(error):
    """Map recognized GitHub failures to safe operator categories only."""
    code = getattr(error, "code", None)
    if type(code) is int:
        if code in (401, 403):
            return "github_authentication_or_access"
        if code == 429:
            return "github_rate_limited"
        if code in (404, 410):
            return "github_pr_or_source_missing"
        if code >= 500:
            return "github_remote_unavailable"
    if isinstance(error, OSError):
        return "github_remote_unavailable"
    # Only our own known self-contained validation messages qualify as a
    # drift signal. Never print raw exception strings from a provider.
    if isinstance(error, ValueError):
        reason = str(error)
        if reason in (
                "PR head/base changed during observation",
                "PR draft state missing or changed during observation",
                "PR review requests changed during observation",
                "check source head drift"):
            return "pr_changed_during_observation"
    return "github_evidence_unavailable_or_invalid"


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
        except (OSError, ValueError) as error:
            result = {
                "url": "https://github.com/" + repo + "/pull/" + str(number),
                "repository": repo, "number": number,
                "status": "github_source_unavailable_or_head_changed",
                "source_problem_kind": _source_problem_kind(error),
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


PR_ARGUMENT = re.compile(r"^Zennay/[A-Za-z0-9_.-]{1,100}#[1-9][0-9]{0,8}$")


def human_gate_summary(source: dict) -> str:
    """Compact offline operator view; observed receipts never authorize merging."""
    if (type(source) is not dict or source.get("schema") != SCHEMA
            or source.get("merge_authorized") is not False
            or source.get("writes_performed") is not False):
        raise ValueError("invalid current-head gate source")
    lines = [
        "zSentinel | actuele PR-gates · uitsluitend waarneming",
        "Bronmoment UTC: " + source["observed_at_utc"],
        "Protected main, verplichte CI, onafhankelijke review en eigenaar: NIET vrijgegeven.",
    ]
    if "source_review_manifest_sha256" in source:
        lines.extend([
            "Review-manifest SHA256: " + source["source_review_manifest_sha256"],
            "Overgenomen bron-board SHA256: " + source["source_review_board_sha256"],
            "Kandidaat-PRs in opgeslagen bron: " + str(source["source_review_pr_count"]),
            "Huidige gecontroleerde bronbatch: " +
                str(source["source_review_batch_index"]) + "/" +
                str(source["source_review_batches_total"]) + " · startref " +
                str(source["source_review_batch_first_pr"]) + " · maximaal " +
                str(source["source_review_batch_count"]) + " PRs",
            "Overige batches afzonderlijk controleren; één JSON is nooit volledige mergevrijgave.",
        ])
    for item in source["items"]:
        lines.append("")
        lines.append("PR: " + item["url"])
        if item.get("status") != "observed_current_head_partial_gate_evidence":
            problem = item.get("source_problem_kind")
            lines.append(SOURCE_PROBLEMS_NL.get(
                problem, "GitHub-bron onbereikbaar of HEAD veranderd; PR niet vrijgeven."))
            lines.append("Geen mergevrijgave zonder hernieuwde exacte HEAD-bron.")
            continue
        lines.append("Waargenomen HEAD: " + item["head_sha_observed"])
        lines.append("Doelbranch: " + item["base_ref_observed"])
        if item["draft_at_read"]:
            lines.append("PR-status: CONCEPT; eigenaar moet deze eerst reviewklaar maken.")
        elif item["merge_conflict_observed"]:
            lines.append("GitHub meldt MERGECONFLICT op huidige base/HEAD; eigenaar moet dit oplossen.")
        elif item["mergeability_not_yet_evaluated"]:
            lines.append("GitHub heeft mergeability nog niet vastgesteld: ONBEKEND.")
        else:
            lines.append("GitHub meldt code samenvoegbaar; dit is GEEN CI/reviewvrijgave.")
        lines.append(
            "Checks: " + str(item["observed_checks_pending"]) + " lopend, " +
            str(item["observed_checks_not_successful"]) + " niet geslaagd")
        lines.append(
            "Queued jobs sinds minstens 30 minuten: " +
            str(item["queued_check_attention_30m_observed"]) +
            " · queued bron zonder valide tijdstip: " +
            str(item["queued_check_age_unknown_observed"]) +
            " (onderzoek runnerroute; geen runneruitval of CI-rerun bewezen).")
        lines.append(
            "Lopende jobs sinds minstens 60 minuten: " +
            str(item["running_check_attention_60m_observed"]) +
            " · in-progress bron zonder valide starttijd: " +
            str(item["running_check_age_unknown_observed"]) +
            " (eigenaartriage; geen vastloper of restart bewezen).")
        for job in item["observed_actionable_check_runs"]:
            if job.get("running_attention_60m") is True:
                lines.append("  Onderzoek langdurige lopende check: " + job["name"] + " · " +
                             str(job["running_age_seconds_observed"] // 60) +
                             " minuten · " + job["url"])
            if job.get("queued_attention_30m") is True:
                lines.append("  Onderzoek queued job: " + job["name"] + " · " +
                             str(job["queued_age_seconds_observed"] // 60) +
                             " minuten · " + job["url"])
        lines.append(
            "Oudere statuscontexten: " +
            str(item["legacy_status_pending_observed"]) + " lopend, " +
            str(item["legacy_status_failure_or_error_observed"]) + " niet geslaagd")
        lines.append(
            "Onafhankelijke exact-head approvals (waargenomen): " +
            str(item["observed_independent_exact_head_approvals"]))
        if item.get("automation_review_events_excluded_from_independent_approval"):
            lines.append(
                "Bot-/automatiseringsreviews niet meegeteld als onafhankelijke " +
                "goedkeuring: " +
                str(item["automation_review_events_excluded_from_independent_approval"]))
        lines.append(
            "Openstaande reviewaanvragen: " +
            str(item["pending_individual_review_requests_observed"]) + " personen, " +
            str(item["pending_team_review_requests_observed"]) + " teams (geen approvals).")
        lines.append("Volgende menselijke actie: " +
                     GATE_STEPS_NL[item["human_next_gate_step"]])
        if item.get("parallel_independent_review_step"):
            lines.append("Parallel mogelijk: " +
                         GATE_STEPS_NL[item["parallel_independent_review_step"]])
        if (item["legacy_status_source_may_be_truncated"] or
                not item["check_runs_page_complete"] or
                item["review_page_may_be_truncated"]):
            lines.append("WAARSCHUWING: huidige bronnen onvolledig of afgekapt.")
        lines.append("Merge-/deployautorisatie: NEE.")
    lines.append("")
    lines.append("Geen claims, herdispatch, merge of deploy uitgevoerd.")
    return "\n".join(lines) + "\n"



def html_gate_summary(source: dict) -> str:
    """Private offline operator desk; no remote actions, scripts, or release grants."""
    if (type(source) is not dict or source.get("schema") != SCHEMA
            or source.get("merge_authorized") is not False
            or source.get("deployment_authorized") is not False
            or source.get("writes_performed") is not False
            or type(source.get("items")) is not list):
        raise ValueError("invalid source for private current-PR gate HTML")

    def h(value):
        return escape(str(value), quote=True)

    # A saved private merge-first manifest can contain many PRs. Make the
    # selected four-item GitHub read window and saved source identity visible;
    # otherwise a standalone HTML report looks like it reviewed the portfolio.
    if "source_review_manifest_sha256" in source:
        digest = source["source_review_manifest_sha256"]
        board_digest = source.get("source_review_board_sha256")
        if (type(digest) is not str or re.fullmatch(r"[a-f0-9]{64}", digest) is None
                or type(board_digest) is not str
                or re.fullmatch(r"[a-f0-9]{64}", board_digest) is None):
            raise ValueError("invalid saved review manifest provenance")
        index = source.get("source_review_batch_index")
        total = source.get("source_review_batches_total")
        candidates = source.get("source_review_pr_count")
        if (any(type(n) is not int or n <= 0 for n in (index, total, candidates))
                or index > total):
            raise ValueError("invalid saved review batch provenance")
        provenance = (
            "<section class='provenance'><h2>Bron en batchafbakening</h2>"
            "<p>Opgeslagen werkbak, <strong>batch " + str(index) + " van " +
            str(total) + "</strong> · kandidaat-PRs in opgeslagen bron: " +
            str(candidates) + ". Deze pagina bevat alleen de geselecteerde "
            "huidige vier of minder exacte-HEAD reads.</p>"
            "<p>Privé reviewmanifest SHA256: <code>" + h(digest) + "</code><br>"
            "Oorspronkelijk board SHA256: <code>" + h(board_digest) +
            "</code></p><p>Vervolg voor ontbrekende batches: afzonderlijk "
            "ophalen. Oude opgeslagen boarddata bewijst geen actuele "
            "claim, brancheigendom of mergevrijgave.</p></section>")
    else:
        provenance = ("<section class='provenance'><h2>Directe PR-selectie</h2>"
                      "<p>Maximaal vier actuele PR-observaties; geen compleet "
                      "portfolio, geen Notion-claimcontrole.</p></section>")
    cards = []
    for item in source["items"]:
        repository, number = item.get("repository"), item.get("number")
        if (type(repository) is not str or REPO.fullmatch(repository) is None
                or type(number) is not int or not 1 <= number <= 999999999):
            raise ValueError("invalid canonical source PR")
        url = "https://github.com/" + repository + "/pull/" + str(number)
        title = "<h2><a rel='noopener noreferrer' href='" + h(url) + "'>" + h(repository) + " #" + str(number) + "</a></h2>"
        if item.get("status") != "observed_current_head_partial_gate_evidence":
            problem = SOURCE_PROBLEMS_NL.get(
                item.get("source_problem_kind"),
                "GitHub-bron niet beschikbaar of HEAD gewijzigd; herlees de bron.")
            cards.append("<article>" + title +
                         "<p class='warning'>" + h(problem) +
                         "</p><p>Geen mergevrijgave; claim en actuele HEAD opnieuw controleren.</p></article>")
            continue
        head = item.get("head_sha_observed")
        if type(head) is not str or SHA.fullmatch(head) is None:
            raise ValueError("untrusted PR commit identity")
        step = item.get("human_next_gate_step")
        if step not in GATE_STEPS_NL:
            raise ValueError("unknown owner gate step")
        action = GATE_STEPS_NL[step]
        parallel_step = item.get("parallel_independent_review_step")
        if parallel_step is not None and parallel_step not in GATE_STEPS_NL:
            raise ValueError("unknown parallel review step")
        state = (
            "CONCEPT: eigenaar moet reviewklaar maken" if item["draft_at_read"] else
            "MERGECONFLICT: eigenaar moet oplossen" if item["merge_conflict_observed"] else
            "Mergeability nog onbekend" if item["mergeability_not_yet_evaluated"] else
            "GitHub ziet code als samenvoegbaar, NIET vrijgegeven")
        badges = (
            "<div class='metrics'>"
            "<span><strong>" + h(item["observed_checks_pending"]) + "</strong> checks niet klaar</span>"
            "<span><strong>" + h(item["observed_checks_not_successful"]) + "</strong> checks niet geslaagd</span>"
            "<span><strong>" + h(item["observed_independent_exact_head_approvals"]) + "</strong> onafhankelijke reviews op HEAD</span>"
            "<span><strong>" + h(item["pending_individual_review_requests_observed"]) + "</strong> reviewverzoeken aan personen</span>"
            "<span><strong>" + h(item["pending_team_review_requests_observed"]) + "</strong> reviewverzoeken aan teams</span>"
            "<span><strong>" + h(item["legacy_status_failure_or_error_observed"]) + "</strong> falende oude statuscontexten</span>"
            "<span><strong>" + h(item["queued_check_attention_30m_observed"]) + "</strong> queued ≥30 min</span>"
            "<span><strong>" + h(item["running_check_attention_60m_observed"]) + "</strong> actief ≥60 min</span>"
            "</div>")
        jobs = []
        for job in item.get("observed_actionable_check_runs", [])[:12]:
            job_id = job.get("check_run_id")
            if type(job_id) is not int or job_id <= 0:
                raise ValueError("untrusted check run identity")
            check_url = "https://github.com/" + repository + "/runs/" + str(job_id)
            kind = ("Lange looptijd, onderzoek eigenaar" if job.get("running_attention_60m") is True
                    else "Lang queued, onderzoek toewijzing" if job.get("queued_attention_30m") is True
                    else "Nog niet klaar of niet geslaagd")
            minutes = (job.get("running_age_seconds_observed")
                       if job.get("running_attention_60m") is True
                       else job.get("queued_age_seconds_observed")
                       if job.get("queued_attention_30m") is True else None)
            duration = (" · " + h(minutes // 60) + " min waargenomen"
                        if type(minutes) is int else "")
            jobs.append("<li><a rel='noopener noreferrer' href='" + h(check_url) +
                        "'>" + h(job.get("name", "Onbekende check")) + "</a> — " +
                        h(kind) + duration + "</li>")
        if item.get("observed_actionable_check_runs_hidden"):
            jobs.append("<li>Extra checkregels buiten beperkte bronweergave: " +
                        h(item["observed_actionable_check_runs_hidden"]) + "</li>")
        legacy = []
        for entry in item.get("observed_actionable_legacy_contexts", [])[:12]:
            legacy.append("<li>" + h(entry.get("context", "Onbekende status")) +
                          " — " + h(entry.get("state", "onbekend")) + "</li>")
        if item.get("observed_actionable_legacy_contexts_hidden"):
            legacy.append("<li>Meer oude statuscontexten buiten venster: " +
                          h(item["observed_actionable_legacy_contexts_hidden"]) + "</li>")
        unknown = []
        if (item.get("legacy_status_source_may_be_truncated") is True
                or item.get("check_runs_page_complete") is not True
                or item.get("review_page_may_be_truncated") is True):
            unknown.append("Een of meer GitHub-bronnen zijn afgekapt.")
        if item.get("queued_check_age_unknown_observed"):
            unknown.append("Queued checks zonder valide tijdstip: " +
                           str(item["queued_check_age_unknown_observed"]))
        if item.get("running_check_age_unknown_observed"):
            unknown.append("Lopende checks zonder valide starttijd: " +
                           str(item["running_check_age_unknown_observed"]))
        if item.get("automation_review_events_excluded_from_independent_approval"):
            unknown.append("Botreviewevents niet als menselijke approvals geteld: " +
                           str(item["automation_review_events_excluded_from_independent_approval"]))
        cards.append(
            "<article>" + title +
            "<p><strong>Huidige HEAD:</strong> <a rel='noopener noreferrer' href='" +
            h("https://github.com/" + repository + "/commit/" + head) + "'><code>" +
            h(head[:12]) + "</code></a> · doel: " + h(item["base_ref_observed"]) + "</p>" +
            "<p class='warning'>" + h(state) + "</p>" + badges +
            "<p class='action'><strong>Volgende eigenaaractie:</strong> " + h(action) + "</p>" +
            ("<p class='action'><strong>Daarnaast parallel:</strong> " + h(GATE_STEPS_NL[parallel_step]) + "</p>"
             if parallel_step else "") +
            ("<h3>Actuele checkacties (begrensde bron)</h3><ul>" + "".join(jobs) + "</ul>" if jobs else "") +
            ("<h3>Legacy statuscontexten</h3><ul>" + "".join(legacy) + "</ul>" if legacy else "") +
            ("<p class='muted'>" + " · ".join(h(x) for x in unknown) + "</p>" if unknown else "") +
            "<p class='muted'>Merge-, claim- en deployautorisatie: NEE. Vereiste branchregels en "
            "volledige verplichte checkset zijn niet onafhankelijk vastgesteld.</p></article>")
    return (
        "<!doctype html><html lang='nl'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; "
        "style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'\">"
        "<title>zSentinel · actuele PR-gates</title><style>"
        "body{font:16px/1.55 system-ui,sans-serif;margin:0 auto;padding:1.5rem;max-width:1100px;"
        "background:#101a26;color:#eef2f6}a{color:#9cceff;overflow-wrap:anywhere}"
        "article{background:#1b2a3a;border:1px solid #44576d;border-radius:1rem;"
        "padding:1.2rem;margin:1.2rem 0}h1,h2,h3{line-height:1.25}"
        ".metrics{display:flex;flex-wrap:wrap;gap:.5rem}"
        ".metrics span{padding:.5rem .7rem;border:1px solid #4a6075;border-radius:.5rem}"
        ".metrics strong{font-size:1.3rem}.action{background:#263e4e;padding:.8rem;border-radius:.5rem}"
        ".provenance{border-left:4px solid #7fbcff;padding:.7rem 1rem;background:#1b2a3a}"
        ".warning{color:#ffe39d}.muted,footer{color:#b5c5d3}li{margin:.3rem 0}"
        "@media print{body{background:#fff;color:#111}article{background:#fff;border-color:#888}"
        "a,.warning,.muted{color:#222}}</style></head><body><header>"
        "<h1>zSentinel · actuele PR-gates</h1><p>Bronmoment UTC: " +
        h(source.get("observed_at_utc", "ONBEKEND")) +
        "</p><p><strong>Read-only eigenaarwerkbak.</strong> Lange queued of lopende checks zijn "
        "uitsluitend signalen, geen bewijs van runneruitval. Onafhankelijke review, exact-head "
        "verplichte CI, eigenaarschap en branch protection blijven altijd vereist.</p></header>" +
        provenance + "".join(cards) +
        "<footer>Geen CI-herstart, queueclaim, PR-merge of live deployment uitgevoerd. "
        "GitHub kan na dit bronmoment veranderen.</footer></body></html>\n")


def markdown_gate_summary(source: dict) -> str:
    """Portable three-line owner handoff per current PR; never approval."""
    if (type(source) is not dict or source.get("schema") != SCHEMA
            or source.get("merge_authorized") is not False
            or source.get("deployment_authorized") is not False
            or source.get("writes_performed") is not False
            or type(source.get("items")) is not list
            or len(source["items"]) > MAX_PRS):
        raise ValueError("invalid source for private Markdown gate handoff")

    lines = [
        "# zSentinel · actueel PR-hersteloverzicht (read-only)",
        "",
        "Bronmoment UTC: " + str(source.get("observed_at_utc", "ONBEKEND")),
        "Dit is geen goedkeuring voor claim, review, CI-retry, merge of deploy.",
    ]
    if "source_review_manifest_sha256" in source:
        fingerprint = source["source_review_manifest_sha256"]
        board = source.get("source_review_board_sha256")
        batch = source.get("source_review_batch_index")
        batches = source.get("source_review_batches_total")
        if (type(fingerprint) is not str or re.fullmatch(r"[a-f0-9]{64}", fingerprint) is None
                or type(board) is not str or re.fullmatch(r"[a-f0-9]{64}", board) is None
                or type(batch) is not int or type(batches) is not int
                or not 1 <= batch <= batches):
            raise ValueError("invalid saved review batch source")
        lines.extend([
            "Opgeslagen reviewbatch: " + str(batch) + "/" + str(batches) +
            " (geen volledige portfoliobron).",
            "Manifest SHA256: `" + fingerprint + "` · board SHA256: `" + board + "`",
        ])
    else:
        lines.append("Directe PR-selectie (maximaal vier, Notion-claims niet gecontroleerd).")
    for item in source["items"]:
        repo, number = item.get("repository"), item.get("number")
        if (type(repo) is not str or REPO.fullmatch(repo) is None
                or type(number) is not int or not 1 <= number <= 999999999):
            raise ValueError("invalid pinned handoff repository")
        url = "https://github.com/" + repo + "/pull/" + str(number)
        lines.extend(["", "## [PR #" + str(number) + "](" + url + ")"])
        if item.get("status") != "observed_current_head_partial_gate_evidence":
            lines.extend([
                "- Probleem: " + SOURCE_PROBLEMS_NL.get(
                    item.get("source_problem_kind"),
                    "GitHub-bron onbereikbaar of HEAD gewijzigd."),
                "- Actie: herlees de actuele PR, eigenaarclaim en verplichte releasegates.",
                "- Status: ONBEKEND; geen automatische actie of merge.",
            ])
            continue
        head = item.get("head_sha_observed")
        step = item.get("human_next_gate_step")
        if (type(head) is not str or SHA.fullmatch(head) is None
                or step not in GATE_STEPS_NL):
            raise ValueError("invalid pinned source or human action")
        if item.get("draft_at_read") is True:
            problem = "PR is nog concept."
        elif item.get("merge_conflict_observed") is True:
            problem = "GitHub meldt mergeconflict op huidige basis."
        elif item.get("observed_checks_not_successful", 0):
            problem = "Mislukte CI-checks op huidige HEAD."
        elif item.get("observed_exact_head_changes_requested", 0):
            problem = "Onafhankelijke reviewer vraagt wijzigingen."
        elif item.get("observed_checks_pending", 0):
            problem = "Bestaande checks staan nog open."
        else:
            problem = "Geldende required checks, review en branchbescherming niet volledig vastgesteld."
        lines.extend([
            "- Probleem: " + problem,
            "- Actie: " + GATE_STEPS_NL[step],
            "- Status: [HEAD `" + head[:12] + "`](https://github.com/" +
                repo + "/commit/" + head + ") · checks pending " +
                str(item["observed_checks_pending"]) + ", mislukt " +
                str(item["observed_checks_not_successful"]) +
                ", onafhankelijk goedgekeurd " +
                str(item["observed_independent_exact_head_approvals"]) +
                "; niet gemerged/geautoriseerd door deze waarneming.",
        ])
        parallel = item.get("parallel_independent_review_step")
        if parallel is not None:
            if parallel not in GATE_STEPS_NL:
                raise ValueError("invalid parallel owner action")
            lines.append("- Parallel: " + GATE_STEPS_NL[parallel])
        # Show the actual failing GitHub check directly, rather than only
        # describing a check failure with no navigable owner work item.
        # Never interpolate provider-supplied names into Markdown link syntax.
        check_rows = item.get("observed_actionable_check_runs", [])
        if type(check_rows) is not list or len(check_rows) > 12:
            raise ValueError("unbounded current-head check preview")
        for job in check_rows:
            job_id = job.get("check_run_id") if type(job) is dict else None
            if type(job_id) is not int or job_id <= 0:
                raise ValueError("invalid current check run ID")
            check_url = "https://github.com/" + repo + "/runs/" + str(job_id)
            if job.get("state") == "completed":
                status = "Controleer niet-geslaagde actuele check"
            elif job.get("running_attention_60m") is True:
                status = "Onderzoek in-progress check >60 min"
            elif job.get("queued_attention_30m") is True:
                status = "Onderzoek queued check >30 min"
            else:
                continue
            lines.append("- CI-eigenaarlink: [" + status + "](" + check_url +
                         "); directe GitHub-check, geen automatische herstart.")
        legacy_rows = item.get("observed_actionable_legacy_contexts", [])
        if type(legacy_rows) is not list or len(legacy_rows) > 12:
            raise ValueError("unbounded legacy context preview")
        for legacy in legacy_rows:
            if type(legacy) is not dict or legacy.get("state") not in (
                    "failure", "error", "pending"):
                raise ValueError("invalid legacy status state")
            context = legacy.get("context")
            if type(context) is not str or not 1 <= len(context) <= 200:
                raise ValueError("invalid legacy check context")
            if legacy["state"] in ("failure", "error"):
                lines.append("- Oude GitHub-status met fout: [bekijk exacte HEAD-status](" +
                             "https://github.com/" + repo + "/commit/" + head +
                             "/checks) (legacy context apart van Check Runs).")
        hidden = item.get("observed_actionable_check_runs_hidden", 0)
        if type(hidden) is not int or hidden < 0:
            raise ValueError("invalid bounded check preview remainder")
        if hidden:
            lines.append("- Meer CI-items buiten dit beperkte venster: " + str(hidden) +
                         "; geen volledige required-check-dekking.")
        if (item.get("legacy_status_source_may_be_truncated") is True
                or item.get("check_runs_page_complete") is not True
                or item.get("review_page_may_be_truncated") is True):
            lines.append("- Brondekking: ONVOLLEDIG; geen releaseconclusie mogelijk.")
    lines.extend(["", "Geen herdispatch, claimovername, CI-bypass of deployment uitgevoerd."])
    return "\n".join(lines) + "\n"


REVIEW_SCHEMA = "zsentinel-offline-merge-first-review-v1"
REVIEW_MAX_BYTES = 1024 * 1024
REVIEW_MAX_PRS = 300
REVIEW_PR_URL = re.compile(
    r"^https://github[.]com/(Zennay/[A-Za-z0-9_.-]{1,100})/pull/([1-9][0-9]{0,8})$")


def read_review_manifest(path: Path, batch: int = 1):
    """Bounded read-only handoff selection; never open a blocking pipe/device.

    Pin a regular file before and after its bounded read. This is a local
    operator input, not evidence of current ownership or merge permission.
    """
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_NONBLOCK"):
        raise ValueError("safe regular-file opening unavailable")
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or not 2 <= before.st_size <= REVIEW_MAX_BYTES:
        raise ValueError("review manifest must be a bounded regular JSON file")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
        if (not stat.S_ISREG(info.st_mode) or
                not 2 <= info.st_size <= REVIEW_MAX_BYTES or
                identity(before) != identity(info)):
            raise ValueError("review manifest changed before read")
        with os.fdopen(os.dup(fd), "rb") as stream:
            raw = stream.read(REVIEW_MAX_BYTES + 1)
        after = os.fstat(fd)
        if len(raw) != info.st_size or identity(after) != identity(info):
            raise ValueError("review manifest changed during read")
    finally:
        os.close(fd)
    if len(raw) > REVIEW_MAX_BYTES:
        raise ValueError("review manifest exceeds one MiB")
    def unique_fields(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError("duplicate review manifest JSON field")
            obj[key] = value
        return obj

    def no_nonfinite(value):
        raise ValueError("non-finite review manifest JSON number")

    try:
        decoded = raw.decode("utf-8", errors="strict")
        # Escaped lone surrogates also create non-UTF-8 source identities.
        data = json.loads(decoded, object_pairs_hook=unique_fields,
                          parse_constant=no_nonfinite)
        json.dumps(data, ensure_ascii=False).encode("utf-8", errors="strict")
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError("invalid or ambiguous UTF-8 JSON review manifest") from None
    if (type(data) is not dict or data.get("schema") != REVIEW_SCHEMA
            or data.get("claim_authorized") is not False
            or data.get("merge_authorized") is not False
            or data.get("deploy_authorized") is not False
            or data.get("dispatch_authorized") is not False
            or data.get("evidence_scope") != "bounded_saved_snapshot_not_live"
            or type(data.get("source_board_sha256")) is not str
            or re.fullmatch(r"[a-f0-9]{64}", data["source_board_sha256"]) is None):
        raise ValueError("not a canonical non-authorizing review manifest")
    rows = data.get("pr_references")
    if type(rows) is not list or not 1 <= len(rows) <= REVIEW_MAX_PRS:
        raise ValueError("review manifest has no bounded PR references")
    priorities = {"P0": 0, "P1": 1, "P2": 2, "Unspecified": 3}
    selected = []
    seen = set()
    for row in rows:
        if (type(row) is not dict or type(row.get("url")) is not str
                or type(row.get("highest_linked_priority")) is not str
                or row["highest_linked_priority"] not in priorities
                or row.get("merge_authorized") is not False
                or type(row.get("observed_issue_refs")) is not list
                or not row["observed_issue_refs"]):
            raise ValueError("invalid captured PR reference")
        match = REVIEW_PR_URL.fullmatch(row["url"])
        if match is None:
            raise ValueError("PR must be a canonical Zennay GitHub reference")
        identity = (match.group(1), int(match.group(2)))
        if identity in seen:
            raise ValueError("duplicate PR in review manifest")
        seen.add(identity)
        selected.append((priorities[row["highest_linked_priority"]], identity))
    selected.sort(key=lambda row: (row[0], row[1]))
    batches = (len(selected) + MAX_PRS - 1) // MAX_PRS
    if type(batch) is not int or not 1 <= batch <= batches:
        raise ValueError("review batch is outside the available saved PR range")
    first = (batch - 1) * MAX_PRS
    return {
        "prs": [identity for _, identity in selected[first:first + MAX_PRS]],
        "source_review_batch_index": batch,
        "source_review_batches_total": batches,
        "source_review_batch_first_pr": first + 1,
        "source_review_batch_count": min(MAX_PRS, len(selected) - first),
        "source_review_manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "source_review_board_sha256": data["source_board_sha256"],
        "source_review_pr_count": len(selected),
        "review_only_top_four_observed": len(selected) > MAX_PRS,
    }


def main(argv=None):
    """Produce one bounded source receipt for the offline merge-first workbench."""
    parser = argparse.ArgumentParser(description=__doc__)
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument(
        "--pr", action="append",
        help="Canonical Zennay/repository#number (repeat one to four times)")
    selector.add_argument(
        "--from-review", type=Path,
        help="Review-only JSON manifest from the offline Merge-first Workbench")
    parser.add_argument(
        "--batch", type=int, default=1,
        help="1-based bounded review manifest batch (four PRs maximum per operation)")
    parser.add_argument(
        "--format", choices=("json", "text", "html", "markdown"), default="json",
        help="JSON receipt (default), Dutch text triage or private offline HTML desk")
    parser.add_argument(
        "--output", type=Path,
        help="Create NEW private 0600 report; omit to print to stdout")
    args = parser.parse_args(argv)
    try:
        review = None
        if args.from_review is not None:
            review = read_review_manifest(args.from_review, batch=args.batch)
            refs = review["prs"]
        else:
            if args.batch != 1:
                raise ValueError("--batch requires --from-review")
            if not 1 <= len(args.pr) <= MAX_PRS:
                raise ValueError("choose one to four PR references")
            refs = []
            for raw in args.pr:
                if type(raw) is not str or PR_ARGUMENT.fullmatch(raw) is None:
                    raise ValueError("PR must use Zennay/repository#number")
                repo, number = raw.rsplit("#", 1)
                refs.append((repo, int(number)))
            if len(set(refs)) != len(refs):
                raise ValueError("duplicate PR reference")
        source = observe_many(refs, token=os.environ.get("GITHUB_TOKEN"))
        if review is not None:
            for key in ("source_review_manifest_sha256",
                        "source_review_board_sha256", "source_review_pr_count",
                        "source_review_batch_index", "source_review_batches_total",
                        "source_review_batch_first_pr", "source_review_batch_count",
                        "review_only_top_four_observed"):
                source[key] = review[key]
        rendered = (
            json.dumps(source, sort_keys=True, ensure_ascii=True, indent=2) + "\n"
            if args.format == "json" else html_gate_summary(source)
            if args.format == "html" else markdown_gate_summary(source)
            if args.format == "markdown" else human_gate_summary(source))
        if args.output is None:
            sys.stdout.write(rendered)
        else:
            write_private_report(args.output, rendered)
            # Never print private source content or path in a shared log.
            print("Created private read-only PR gate receipt (0600); no merges or GitHub writes.",
                  file=sys.stderr)
        return 0
    except OSError:
        # A failed private output may contain its local path in OSError text.
        # Do not echo that location to shared terminal or worker logs.
        print("zSentinel PR gate desk refused: GitHub or private output unavailable",
              file=sys.stderr)
        return 2
    except ValueError as exc:
        print("zSentinel PR gate desk refused: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

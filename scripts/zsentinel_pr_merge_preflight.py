#!/usr/bin/env python3
"""Source-bound, bounded standalone GitHub PR merge-first owner preflight.

Read-only: this is not a merge authorization, branch-protection check,
independent approval, claim check, CI trigger, queue update or deployment.
"""
from __future__ import annotations

import html
import json
import re
from urllib.parse import quote

from zsentinel_pr_live_read import (
    GitHubReadError, MAX_LIST_PAGE, REPO_URL, _checks_summary,
    _limited_json, _pr_summary, _review_summary, _review_request_identities, utc,
)


def _known(value):
    """Render missing source facts as UNKNOWN without hiding a real False."""
    return "UNKNOWN" if value is None else str(value)


def _review_followup(observed, reviews, source_stable):
    """One actionable read-only reviewer follow-up, never approval evidence."""
    unknown = {
        "state": "UNKNOWN",
        "next": "Inspect GitHub reviews and pending requests before contacting reviewers.",
        "independent_review_verified": False,
        "external_action_executed": False,
    }
    if not source_stable or type(observed) is not dict or type(reviews) is not dict:
        return unknown
    submissions = reviews.get("sampled_count")
    if type(submissions) is not int or submissions < 0:
        return unknown
    if submissions:
        return {
            "state": "REVIEW_SUBMISSIONS_OBSERVED",
            "next": "Inspect current effective review decisions on this exact HEAD and open threads; submissions alone are not approval.",
            "independent_review_verified": False,
            "external_action_executed": False,
        }
    users, teams = (
        observed.get("pending_reviewer_requests"),
        observed.get("pending_team_review_requests"),
    )
    if type(users) is not int or type(teams) is not int or users < 0 or teams < 0:
        return unknown
    if users + teams:
        return {
            "state": "PENDING_REQUESTS_OBSERVED",
            "next": "Follow up on existing GitHub reviewer requests; do not send duplicate requests.",
            "independent_review_verified": False,
            "external_action_executed": False,
        }
    return {
        "state": "NO_REVIEW_REQUESTS_OBSERVED",
        "next": "Request independent review from an authorized owner and verify exact-HEAD approval before merge.",
        "independent_review_verified": False,
        "external_action_executed": False,
    }


def _repository_default_branch(payload, full_name):
    """Read one canonical repository identity and its current default branch."""
    if not isinstance(payload, dict) or payload.get("full_name") != full_name:
        raise GitHubReadError("repository metadata identity is inconsistent")
    branch = payload.get("default_branch")
    if (not isinstance(branch, str) or not branch or len(branch) > 255
            or branch != branch.strip()
            or any(not char.isprintable() or char.isspace() for char in branch)):
        raise GitHubReadError("repository default branch is missing or invalid")
    return branch


def _repository_default_branch_sha(payload, branch):
    """Read the exact tip of the named branch without trusting a PR's base hint."""
    if not isinstance(payload, dict) or payload.get("name") != branch:
        raise GitHubReadError("default branch identity is inconsistent")
    commit = payload.get("commit")
    sha = commit.get("sha") if isinstance(commit, dict) else None
    if not isinstance(sha, str) or not re.fullmatch(r"[a-f0-9]{40}", sha):
        raise GitHubReadError("default branch tip SHA is missing or invalid")
    return sha


def _gate_cards(head, checks, reviews, stable, default_branch=None,
                default_branch_stable=False, default_branch_sha=None,
                default_branch_sha_stable=False, base_tip_matches_default=None,
                mergeability_hint_stable=False, branch_protection=None,
                branch_protection_stable=False):
    """Operator workorders; read-only samples never satisfy release gates."""
    state = ("OBSERVED" if stable else "UNKNOWN")
    sample = ("UNKNOWN" if checks is None else
              "OBSERVED_BLOCKING_NON_SUCCESS" if checks["sampled_blocking_checks"] else
              "OBSERVED_PENDING" if checks["sampled_pending_checks"] else
              "OBSERVED_NEUTRAL_OR_SKIPPED" if checks["sampled_non_success_checks"] else
              "SAMPLE_ONLY")
    branch_gate = (
        "UNKNOWN" if not default_branch_stable or not default_branch else
        "OBSERVED" if stable and head and
        head["observed_state"] == "open" and
        not head["observed_draft"] and
        not head["reported_merged"] and
        head["target_branch"] == default_branch else "HOLD"
    )
    base_tip_gate = (
        "NOT_APPLICABLE" if head and default_branch and
        head["target_branch"] != default_branch else
        "UNKNOWN" if not default_branch_stable or not default_branch_sha_stable
        or not default_branch_sha else
        "OBSERVED_CURRENT" if base_tip_matches_default is True else "HOLD"
    )
    mergeability_hint = (
        head.get("mergeable_hint") if isinstance(head, dict) else None
    )
    mergeability_gate = (
        "UNKNOWN" if not mergeability_hint_stable or mergeability_hint is None else
        "OBSERVED_CONFLICT_HINT" if mergeability_hint is False else
        "OBSERVED_MERGEABLE_HINT"
    )
    return [
        {"gate": "exact_head", "state": state,
         "next": "Reconfirm current exact SHA at the time of authorized merge."},
        {"gate": "pr_open_non_draft_on_default_branch", "state": branch_gate,
         "next": "Target the observed repository default branch (" +
                 (default_branch or "UNKNOWN") +
                 ") and independently confirm intended release scope."},
        {"gate": "base_tip_current", "state": base_tip_gate,
         "next": "Compare the PR base SHA with the stable default-branch tip; if stale, update the existing branch and resolve conflicts without force or gate bypass."},
        {"gate": "mergeability_hint", "state": mergeability_gate,
         "next": "GitHub's mergeability value is an asynchronous hint only. If false, inspect conflicts with the existing branch owner; true never replaces exact-head CI, independent review, branch protection or owner approval."},
        {"gate": "head_check_sample", "state": sample,
         "next": "Resolve visible failures and pending jobs; independently determine mandatory checks."},
        {"gate": "all_required_ci", "state": "UNKNOWN",
         "next": "Confirm every branch-required check on this exact SHA."},
        {"gate": "independent_review_and_threads", "state": "UNKNOWN",
         "next": "Confirm latest independent approval and zero unresolved review threads.",
         "sample_available": reviews is not None},
        {"gate": "branch_protection",
         "state": "CONFIGURATION_OBSERVED_NOT_SATISFIED" if branch_protection_stable and isinstance(branch_protection, dict) else "UNKNOWN",
         "next": ("A stable protection configuration was observed, but this is not proof that this PR satisfies every required rule; confirm exact-head required checks, effective review, threads, claims and owner authority."
                  if branch_protection_stable and isinstance(branch_protection, dict)
                  else "Protection configuration could not be stably observed; do not infer that the branch is unprotected or that merge is allowed.")},
        {"gate": "claim_and_owner", "state": "UNKNOWN",
         "next": "Check active worker/Notion claims and get explicit owner authorization."},
        {"gate": "default_branch_merge_and_live", "state": "NOT_EXECUTED",
         "next": "After authorized merge verify merged=true, exact current default-branch SHA and separate live deploy."},
    ]


MAX_PARALLEL_OWNER_ACTIONS = 12


def _parallel_owner_actions(report):
    """Expose independent merge blockers without turning samples into authority.

    A stale base must not hide a pending check or a review request. These
    bounded cards are suggested *human* work, never scheduler instructions.
    """
    observed = report.get("observed") or {}
    checks = report.get("sampled_check_runs")
    reviews = report.get("sampled_reviews")
    cards = []

    def add(code, problem, action, evidence):
        cards.append({
            "code": code, "problem": problem, "owner_action": action,
            "evidence": evidence, "status": "HOLD_OWNER_REVIEW",
            "execution_authorized": False,
        })

    if not report.get("source_stable_between_reads"):
        add("refresh_unstable_pr_source",
            "The exact PR or default-branch source was not stable.",
            "Restore source admission and re-observe before doing PR work.",
            "SOURCE_UNKNOWN")
        return cards

    if observed.get("reported_merged") is True:
        add("verify_reported_merge_on_default",
            "GitHub reports this PR already merged.",
            "Read the exact merged default-branch SHA and verify live release separately.",
            "ALREADY_REPORTED_MERGED")
        return cards
    if observed.get("observed_state") != "open":
        add("find_existing_open_product_pr",
            "This PR is not open and cannot be a current merge candidate.",
            "Inspect its closure and find a genuinely open owner-controlled product PR.",
            "CLOSED_UNMERGED")
        return cards

    if report.get("target_is_default_branch") is False:
        add("confirm_default_branch_target",
            "The PR does not target the observed default branch.",
            "Confirm the intended release branch with the PR owner.",
            "BASE_NOT_DEFAULT")
    elif report.get("base_tip_matches_default") is False:
        add("reconcile_outdated_pr_base",
            "The PR base differs from the stable default-branch tip.",
            "Coordinate a non-force update and conflict resolution with the branch owner.",
            "BASE_TIP_OUTDATED")
    elif report.get("base_tip_matches_default") is None:
        add("recheck_default_branch_tip",
            "The current default-branch base could not be established.",
            "Recover the stable branch-name and tip source.",
            "DEFAULT_BRANCH_TIP_UNKNOWN")
    history = report.get("base_head_history")
    if history is None:
        add("refresh_exact_base_head_compare",
            "Exact base/head commit history could not be established.",
            "Refresh the bounded compare for the observed immutable base and head SHAs; do not infer a clean branch history.",
            "BRANCH_HISTORY_UNKNOWN")
    elif history["status"] == "diverged":
        add("reconcile_diverged_pr_history",
            "The PR head and base have unique commits on both sides (ahead_by=" +
            str(history["ahead_by"]) + ", behind_by=" + str(history["behind_by"]) + ").",
            "Coordinate a non-force update with the existing branch owner, then refresh the exact compare and all head-bound gates.",
            "BRANCH_HISTORY_DIVERGED")
    elif history["status"] == "behind":
        add("update_pr_head_from_current_base",
            "The PR head is behind its observed base.",
            "Update the existing branch with its owner and refresh exact-head gates before reconsidering merge.",
            "BRANCH_HISTORY_BEHIND")
    elif history["status"] == "identical":
        add("confirm_pr_has_unique_commits",
            "The PR head is identical to its observed base and has no unique commits.",
            "Confirm whether the product change is already present; do not merge an empty candidate.",
            "BRANCH_HAS_NO_UNIQUE_COMMITS")
    if observed.get("observed_draft") is True:
        add("finish_existing_draft",
            "The PR is still draft.",
            "Ask its current owner to mark ready only after review preparation.",
            "DRAFT")
    mergeability_hint = observed.get("mergeable_hint")
    if report.get("mergeability_hint_stable") is not True:
        add("refresh_mergeability_hint",
            "GitHub's mergeability hint changed between the two PR reads.",
            "Refresh the current PR metadata and inspect conflicts; do not infer mergeability from a moving hint.",
            "MERGEABILITY_HINT_UNSTABLE")
    elif mergeability_hint is False:
        add("inspect_merge_conflict_hint",
            "GitHub reports mergeable=false for the observed PR.",
            "Inspect the actual conflicting paths and resolve them with the existing branch owner; never force-push or bypass required gates.",
            "GITHUB_MERGEABLE_FALSE_HINT")
    elif mergeability_hint is None:
        add("refresh_mergeability_hint",
            "GitHub did not provide a mergeability hint.",
            "Re-read current PR metadata and inspect conflicts; a missing hint is not evidence of mergeability.",
            "MERGEABILITY_UNKNOWN")
    if checks is None:
        add("recover_head_check_source",
            "Exact-head check source is unavailable.",
            "Restore source visibility; do not infer a green gate.",
            "CHECK_SOURCE_UNKNOWN")
    else:
        if checks.get("sampled_blocking_checks"):
            add("investigate_sampled_terminal_checks",
                "Some sampled exact-head checks have terminal non-success.",
                "Inspect linked checks; resolve real failures without weakening requirements.",
                "OBSERVED_BLOCKING_NON_SUCCESS")
        if checks.get("sampled_pending_checks"):
            add("resolve_pending_head_checks",
                "Some sampled exact-head checks are pending or queued.",
                "Reconcile their actual runner/admission state; no blind workflow reruns.",
                "OBSERVED_PENDING_CHECKS")
        if checks.get("sampled_non_success_checks"):
            add("inspect_neutral_or_skipped_checks",
                "Some sampled exact-head checks are neutral, skipped or otherwise non-success.",
                "Confirm the effective required-check contract in GitHub; neutral or skipped samples do not prove success.",
                "OBSERVED_NON_SUCCESS_CHECKS")
        if checks.get("sample_incomplete"):
            add("review_unsampled_head_checks",
                "GitHub reports more check runs than this bounded sample contains.",
                "Inspect all branch-required exact-head checks independently.",
                "SAMPLE_INCOMPLETE")
    if reviews is None:
        add("recover_review_source",
            "The bounded PR review source is unavailable.",
            "Restore read-only review visibility and inspect independent approval.",
            "REVIEW_SOURCE_UNKNOWN")
    elif reviews.get("sampled_count") == 0:
        requested_users = observed.get("pending_reviewer_requests")
        requested_teams = observed.get("pending_team_review_requests")
        if requested_users is not None and requested_teams is not None:
            if requested_users + requested_teams:
                add("await_requested_independent_review",
                    "No submitted reviews yet; GitHub has pending user/team review requests.",
                    "Follow up on the existing review requests without issuing duplicates; "
                    "independently check eventual approval on the exact HEAD and all threads.",
                    "PENDING_REVIEW_REQUESTS")
            else:
                add("request_independent_review",
                    "No submitted review or pending review request was observed.",
                    "Ask the owner to request an independent review; verify approval, "
                    "head relevance and unresolved threads before merge.",
                    "ZERO_REVIEWS_AND_REQUESTS_IN_SAMPLE")
        else:
            add("inspect_review_request_source",
                "No submitted reviews; pending reviewer request data is unavailable.",
                "Open the PR review panel before requesting another reviewer; "
                "missing data does not mean nobody has been asked.",
                "REVIEW_REQUEST_SOURCE_UNKNOWN")
    elif reviews.get("exact_head_review_coverage") != "OBSERVED":
        add("reconcile_partial_review_coverage",
            "Some sampled review entries are unbound, unknown or truncated.",
            "Inspect effective reviewer decisions and unresolved threads on GitHub.",
            "PARTIAL_REVIEW_SAMPLE")
    else:
        add("verify_effective_independent_review",
            "Review submissions were observed, but no effective independent approval or resolved-thread state is proven.",
            "Inspect latest per-reviewer decisions on the exact HEAD and all unresolved review threads before seeking merge.",
            "REVIEW_SUBMISSIONS_NOT_APPROVAL")
    protection_stable = report.get("branch_protection_config_stable") is True
    add("verify_authority_and_protection",
        ("A stable branch-protection configuration was observed, but satisfaction of its rules, current claims and release-owner permission remain unverified."
         if protection_stable else
         "Branch-protection configuration could not be stably observed; current claims and release-owner permission also remain unverified."),
        "Check exact-head required CI, effective independent review, unresolved threads, active claims and release-owner permission before merge; never infer protection state from an inaccessible endpoint.",
        "PROTECTION_CONFIG_OBSERVED_GATES_UNVERIFIED" if protection_stable else "HUMAN_GATES_UNKNOWN")
    # Preserve independent CI and reviewer gates when many issues compete for
    # the bounded 12-card display. A truncated shortlist must explicitly say so.
    # This remains a human-owned HOLD; it does not grant merge authority.
    priority = {
        "investigate_sampled_terminal_checks", "resolve_pending_head_checks",
        "inspect_neutral_or_skipped_checks", "recover_head_check_source",
        "request_independent_review", "await_requested_independent_review",
        "reconcile_partial_review_coverage",
        "verify_effective_independent_review", "recover_review_source",
        "inspect_review_request_source",
    }
    authority_card = cards[-1]
    candidates = cards[:-1]
    ordered = ([card for card in candidates if card["code"] in priority] +
               [card for card in candidates if card["code"] not in priority])
    if len(ordered) <= MAX_PARALLEL_OWNER_ACTIONS - 1:
        return ordered + [authority_card]
    shown = ordered[:MAX_PARALLEL_OWNER_ACTIONS - 2]
    hidden = ordered[len(shown):]
    hidden_codes = [item["code"] for item in hidden]
    hidden_sample = hidden_codes[:8]
    hidden_suffix = (" (+" + str(len(hidden_codes) - len(hidden_sample)) +
                     " further)" if len(hidden_codes) > len(hidden_sample) else "")
    return shown + [{
        "code": "additional_owner_holds_omitted",
        "problem": (str(len(hidden)) + " additional owner HOLD action(s) exceed the "
                    "bounded display. Hidden action codes: " +
                    ", ".join(hidden_sample) + hidden_suffix + "."),
        "omitted_action_count": len(hidden),
        "omitted_action_codes_sample": hidden_sample,
        "omitted_action_codes_truncated": len(hidden_codes) > len(hidden_sample),
        "owner_action": "Inspect the complete PR, GitHub checks, review requests, base/head history and source gaps; omitted cards are not clearances.",
        "evidence": "BOUNDED_DISPLAY_INCOMPLETE",
        "status": "HOLD_OWNER_REVIEW",
        "execution_authorized": False,
    }, authority_card]


def _compare_summary(payload, expected_base_sha, expected_head_sha):
    """Validate the bounded exact-SHA GitHub compare response."""
    if type(payload) is not dict:
        raise GitHubReadError("base/head compare response is not an object")
    base = payload.get("base_commit")
    head = payload.get("head_commit")
    # A correct base alone is not sufficient: a cached, mistargeted or
    # stale compare response must not authenticate a different PR HEAD.
    if type(base) is not dict or base.get("sha") != expected_base_sha:
        raise GitHubReadError("base/head compare base identity changed")
    if type(head) is not dict or head.get("sha") != expected_head_sha:
        raise GitHubReadError("base/head compare head identity changed")
    status = payload.get("status")
    ahead = payload.get("ahead_by")
    behind = payload.get("behind_by")
    total = payload.get("total_commits")
    if (status not in ("ahead", "behind", "diverged", "identical")
            or type(ahead) is not int or not 0 <= ahead <= 10**9
            or type(behind) is not int or not 0 <= behind <= 10**9
            or type(total) is not int or not 0 <= total <= 10**9):
        raise GitHubReadError("base/head compare counters or status are invalid")
    consistent = (
        (status == "ahead" and ahead > 0 and behind == 0)
        or (status == "behind" and ahead == 0 and behind > 0)
        or (status == "diverged" and ahead > 0 and behind > 0)
        or (status == "identical" and ahead == 0 and behind == 0)
    )
    if not consistent:
        raise GitHubReadError("base/head compare status disagrees with its counters")
    return {
        "schema": "zsentinel-exact-pr-history-v1",
        "status": status,
        "ahead_by": ahead,
        "behind_by": behind,
        "total_commits": total,
        "base_sha": expected_base_sha,
        "head_sha": expected_head_sha,
        "comparison_source": "github_compare_exact_commit_shas",
        "merge_authorized": False,
    }



def _branch_protection_summary(payload, full_name, branch):
    """Normalize a real branch-protection response without calling it merge approval."""
    if type(payload) is not dict:
        raise GitHubReadError("branch protection response is not an object")
    if not isinstance(full_name, str) or not re.fullmatch(
        r"Zennay/[A-Za-z0-9_.-]{1,100}", full_name
    ):
        raise GitHubReadError("branch protection repository identity is invalid")
    if not isinstance(branch, str) or not branch or len(branch) > 255:
        raise GitHubReadError("branch protection branch identity is invalid")

    required = payload.get("required_status_checks")
    contexts = []
    strict = None
    if required is not None:
        if type(required) is not dict:
            raise GitHubReadError("required status-check configuration is malformed")
        raw_contexts = required.get("contexts", [])
        raw_checks = required.get("checks", [])
        strict = required.get("strict")
        if type(raw_contexts) is not list or len(raw_contexts) > 64:
            raise GitHubReadError("required status-check contexts are unbounded")
        if type(raw_checks) is not list or len(raw_checks) > 64:
            raise GitHubReadError("required status-check app list is unbounded")
        if strict is not None and type(strict) is not bool:
            raise GitHubReadError("required status-check strict flag is malformed")
        for context in raw_contexts:
            if not isinstance(context, str) or not context.strip() or len(context) > 200:
                raise GitHubReadError("required status-check context is malformed")
            contexts.append(context.strip())
        for item in raw_checks:
            if type(item) is not dict:
                raise GitHubReadError("required status-check app entry is malformed")
            context = item.get("context")
            app_id = item.get("app_id")
            if not isinstance(context, str) or not context.strip() or len(context) > 200:
                raise GitHubReadError("required status-check app context is malformed")
            if app_id is not None and (type(app_id) is not int or app_id < 0):
                raise GitHubReadError("required status-check app identity is malformed")
            contexts.append(context.strip())

    reviews = payload.get("required_pull_request_reviews")
    if reviews is not None:
        if type(reviews) is not dict:
            raise GitHubReadError("required review configuration is malformed")
        approvals = reviews.get("required_approving_review_count")
        if approvals is not None and (type(approvals) is not int or not 0 <= approvals <= 100):
            raise GitHubReadError("required approval count is malformed")
        for key in ("dismiss_stale_reviews", "require_code_owner_reviews", "require_last_push_approval"):
            if key in reviews and type(reviews[key]) is not bool:
                raise GitHubReadError("required review boolean is malformed")

    def enabled_object(key):
        if key not in payload:
            return None
        value = payload[key]
        if value is None:
            return False
        if type(value) is not dict or type(value.get("enabled")) is not bool:
            raise GitHubReadError("branch protection flag is malformed: " + key)
        return value["enabled"]

    restrictions = payload.get("restrictions")
    if restrictions is not None:
        if type(restrictions) is not dict:
            raise GitHubReadError("push restrictions are malformed")
        for key in ("users", "teams", "apps"):
            values = restrictions.get(key, [])
            if type(values) is not list or len(values) > 200:
                raise GitHubReadError("push restriction list is malformed: " + key)
            if any(type(value) is not dict for value in values):
                raise GitHubReadError("push restriction entry is malformed: " + key)
    return {
        "schema_version": "zsentinel-branch-protection-observation-v1",
        "repository": full_name,
        "branch": branch,
        "branch_protected": True,
        "required_status_checks_enabled": required is not None,
        "required_status_check_contexts": sorted(set(contexts)),
        "required_status_checks_strict": strict,
        "required_pull_request_reviews_enabled": reviews is not None,
        "required_approving_review_count": (
            reviews.get("required_approving_review_count") if isinstance(reviews, dict) else None
        ),
        "dismiss_stale_reviews": (
            reviews.get("dismiss_stale_reviews") if isinstance(reviews, dict) else None
        ),
        "require_code_owner_reviews": (
            reviews.get("require_code_owner_reviews") if isinstance(reviews, dict) else None
        ),
        "require_last_push_approval": (
            reviews.get("require_last_push_approval") if isinstance(reviews, dict) else None
        ),
        "enforce_admins": enabled_object("enforce_admins"),
        "allow_force_pushes": enabled_object("allow_force_pushes"),
        "allow_deletions": enabled_object("allow_deletions"),
        "required_conversation_resolution": enabled_object("required_conversation_resolution"),
        "required_linear_history": enabled_object("required_linear_history"),
        "push_restrictions_enabled": restrictions is not None,
        "push_restriction_counts": ({
            key: len(restrictions.get(key, [])) for key in ("users", "teams", "apps")
        } if isinstance(restrictions, dict) else None),
        "merge_authorized": False,
        "branch_protection_verified": False,
    }


def observe_exact_pr(pr_url, *, token=None):
    """At most eleven bounded GETs; pin PR, default-branch name, tip and protection config to stable reads."""
    match = REPO_URL.fullmatch(pr_url) if isinstance(pr_url, str) else None
    if match is None or len(pr_url) > 250:
        raise GitHubReadError("provide one canonical Zennay GitHub pull-request URL")
    full_name = match[1] + "/" + match[2]
    number = int(match[3])
    if not 1 <= number <= 100000000:
        raise GitHubReadError("PR number is out of bounds")
    api = "/repos/" + quote(match[1], safe="") + "/" + quote(match[2], safe="")
    path = api + "/pulls/" + str(number)
    first_payload = _limited_json(path, token=token)
    first = _pr_summary(first_payload, full_name, number)
    first_requested = _review_request_identities(first_payload)
    del first_payload
    checks = None
    reviews = None
    missing = []
    attempted = 1
    default_branch = None
    default_branch_stable = False
    default_branch_sha = None
    default_branch_sha_stable = False
    branch_protection_first = None
    branch_protection = None
    branch_protection_stable = False
    try:
        attempted += 1
        default_branch = _repository_default_branch(
            _limited_json(api, token=token), full_name,
        )
    except GitHubReadError as exc:
        missing.append("repository metadata: " + str(exc))
        if "authentication, quota or provider admission" in str(exc):
            return _unknown_pr(pr_url, first, missing, attempted)

    if default_branch is not None:
        try:
            attempted += 1
            branch_protection_first = _branch_protection_summary(
                _limited_json(
                    api + "/branches/" + quote(default_branch, safe="") + "/protection",
                    token=token,
                ),
                full_name,
                default_branch,
            )
        except GitHubReadError as exc:
            missing.append("branch protection configuration: " + str(exc))
            if "authentication, quota or provider admission" in str(exc):
                return _unknown_pr(
                    pr_url, first, missing, attempted,
                    default_branch=default_branch,
                )
        try:
            attempted += 1
            default_branch_sha = _repository_default_branch_sha(
                _limited_json(
                    api + "/branches/" + quote(default_branch, safe=""),
                    token=token,
                ),
                default_branch,
            )
        except GitHubReadError as exc:
            missing.append("default branch tip: " + str(exc))
            if "authentication, quota or provider admission" in str(exc):
                return _unknown_pr(
                    pr_url, first, missing, attempted,
                    default_branch=default_branch,
                )

    try:
        attempted += 1
        checks = _checks_summary(_limited_json(
            api + "/commits/" + first["exact_head_sha"] +
            "/check-runs?per_page=" + str(MAX_LIST_PAGE), token=token,
        ), first["exact_head_sha"], full_name)
    except GitHubReadError as exc:
        missing.append("check-runs: " + str(exc))
        if "authentication, quota or provider admission" in str(exc):
            return _unknown_pr(pr_url, first, missing, attempted, default_branch=default_branch)

    try:
        attempted += 1
        reviews = _review_summary(_limited_json(
            path + "/reviews?per_page=" + str(MAX_LIST_PAGE), token=token,
        ), first["exact_head_sha"])
    except GitHubReadError as exc:
        missing.append("reviews: " + str(exc))
        if "authentication, quota or provider admission" in str(exc):
            return _unknown_pr(pr_url, first, missing, attempted, default_branch=default_branch)

    if default_branch is not None:
        try:
            attempted += 1
            second_default_branch = _repository_default_branch(
                _limited_json(api, token=token), full_name,
            )
            if second_default_branch != default_branch:
                return _unknown_pr(
                    pr_url, first,
                    ["repository default branch changed during observation; "
                     "no stable release target was established"], attempted,
                    default_branch=default_branch,
                    default_branch_sha=default_branch_sha,
                )
            default_branch_stable = True
        except GitHubReadError as exc:
            missing.append("repository default-branch re-read: " + str(exc))
            if "authentication, quota or provider admission" in str(exc):
                return _unknown_pr(
                    pr_url, first, missing, attempted,
                    default_branch=default_branch,
                    default_branch_sha=default_branch_sha,
                )

    if default_branch is not None and branch_protection_first is not None:
        try:
            attempted += 1
            second_branch_protection = _branch_protection_summary(
                _limited_json(
                    api + "/branches/" + quote(default_branch, safe="") + "/protection",
                    token=token,
                ),
                full_name,
                default_branch,
            )
            if second_branch_protection != branch_protection_first:
                missing.append("branch protection configuration changed during observation; configuration is UNKNOWN")
            else:
                branch_protection = branch_protection_first
                branch_protection_stable = True
        except GitHubReadError as exc:
            missing.append("branch protection configuration re-read: " + str(exc))
            if "authentication, quota or provider admission" in str(exc):
                return _unknown_pr(
                    pr_url, first, missing, attempted,
                    default_branch=default_branch,
                    default_branch_sha=default_branch_sha,
                )

    if default_branch is not None and default_branch_sha is not None:
        try:
            attempted += 1
            second_default_branch_sha = _repository_default_branch_sha(
                _limited_json(
                    api + "/branches/" + quote(default_branch, safe=""),
                    token=token,
                ),
                default_branch,
            )
            if second_default_branch_sha != default_branch_sha:
                return _unknown_pr(
                    pr_url, first,
                    ["default branch tip changed during observation; "
                     "no stable base comparison was established"], attempted,
                    default_branch=default_branch,
                    default_branch_sha=default_branch_sha,
                )
            default_branch_sha_stable = True
        except GitHubReadError as exc:
            missing.append("default branch tip re-read: " + str(exc))
            if "authentication, quota or provider admission" in str(exc):
                return _unknown_pr(
                    pr_url, first, missing, attempted,
                    default_branch=default_branch,
                    default_branch_sha=default_branch_sha,
                )

    attempted += 1
    try:
        second_payload = _limited_json(path, token=token)
        second = _pr_summary(second_payload, full_name, number)
        second_requested = _review_request_identities(second_payload)
        del second_payload
    except GitHubReadError as exc:
        missing.append("final PR re-read: " + str(exc))
        return _unknown_pr(
            pr_url, first, missing, attempted,
            default_branch=default_branch,
            default_branch_sha=default_branch_sha,
        )
    stable_keys = (
        "number", "observed_state", "observed_draft", "reported_merged",
        "target_branch", "base_sha", "exact_head_sha",
        "head_repo_full_name", "head_ref",
        "pending_reviewer_requests", "pending_team_review_requests",
    )
    if ((first_requested is not None and second_requested is not None
         and first_requested != second_requested)
            or any(first[k] != second[k] for k in stable_keys)):
        return _unknown_pr(
            pr_url, first,
            ["PR state, source ref or pending reviewer requests changed between "
             "source reads; re-observe without re-requesting review"], attempted,
        )

    history_comparison = None
    if first["target_branch"] == default_branch:
        comparison_base_sha = (
            default_branch_sha if default_branch_sha_stable else None
        )
    else:
        comparison_base_sha = first["base_sha"]
    if comparison_base_sha is None:
        missing.append("exact base/head compare: stable comparison base unavailable")
    else:
        attempted += 1
        compare_path = (
            api + "/compare/" + quote(comparison_base_sha, safe="") + "..." +
            quote(first["exact_head_sha"], safe="") + "?per_page=1"
        )
        try:
            history_comparison = _compare_summary(
                _limited_json(compare_path, token=token),
                comparison_base_sha, first["exact_head_sha"],
            )
            # A human navigation link is useful for the existing PR owner,
            # but is never evidence of mergeability or release authority.
            history_comparison["browser_url"] = (
                "https://github.com/" + full_name + "/compare/" +
                comparison_base_sha + "..." + first["exact_head_sha"]
            )
        except GitHubReadError as exc:
            missing.append("exact base/head compare: " + str(exc))

    if first["reported_merged"]:
        next_step = "Verify GitHub merged=true and the exact default-branch commit; release is separate."
        status = "ALREADY_REPORTED_MERGED"
    elif first["observed_state"] != "open":
        next_step = "Find an existing open replacement PR; the closed PR is not a merge candidate."
        status = "CLOSED_UNMERGED"
    elif default_branch is None or not default_branch_stable:
        next_step = "Restore repository-metadata visibility and confirm the current default branch before merge review."
        status = "DEFAULT_BRANCH_SOURCE_UNKNOWN"
    elif first["target_branch"] != default_branch:
        next_step = "This PR targets " + first["target_branch"] + ", not the observed default branch " + default_branch + "; confirm intended release scope."
        status = "BASE_NOT_DEFAULT"
    elif default_branch_sha is None or not default_branch_sha_stable:
        next_step = "Restore a stable read of the current default-branch tip before evaluating whether this PR is up to date."
        status = "DEFAULT_BRANCH_TIP_UNKNOWN"
    elif first["base_sha"] != default_branch_sha:
        next_step = (
            "The PR base SHA differs from the stable current default-branch tip. "
            "Update the existing branch with its owner and resolve conflicts without force or gate bypass."
        )
        status = "BASE_TIP_OUTDATED"
    elif first["observed_draft"]:
        next_step = "Finish the existing draft with its owner; keep independent review and CI gates."
        status = "DRAFT"
    elif history_comparison is None:
        next_step = "The exact base/head commit comparison is unavailable. Refresh the stable base/head compare before considering merge."
        status = "BRANCH_HISTORY_UNKNOWN"
    elif history_comparison["status"] == "diverged":
        next_step = (
            "The exact PR head and its current base have diverged (ahead_by=" +
            str(history_comparison["ahead_by"]) + ", behind_by=" +
            str(history_comparison["behind_by"]) + "). Reconcile the existing branch with its owner without force-push or gate bypass."
        )
        status = "BRANCH_HISTORY_DIVERGED"
    elif history_comparison["status"] == "behind":
        next_step = "The PR head is behind its current base. Update the existing branch with its owner and refresh exact-head gates; do not merge stale history."
        status = "BRANCH_HISTORY_BEHIND"
    elif history_comparison["status"] == "identical":
        next_step = "The PR head has no unique commits relative to its current base. Confirm the intended product diff and reuse/close an empty candidate rather than treating it as deliverable."
        status = "BRANCH_HAS_NO_UNIQUE_COMMITS"
    elif checks is not None and checks["sampled_blocking_checks"]:
        next_step = "Investigate potentially blocking terminal non-success conclusions (failure/cancelled/stale/unknown); repair confirmed causes without bypassing required checks."
        status = "OBSERVED_BLOCKING_NON_SUCCESS"
    elif checks is not None and checks["sampled_pending_checks"]:
        next_step = "Wait for exact-head checks or resolve their actual runner/admission dependency."
        status = "OBSERVED_PENDING_CHECKS"
    elif checks is not None and checks["sampled_non_success_checks"]:
        next_step = "Inspect neutral/skipped terminal check conclusions against actual branch protection; they are not success evidence."
        status = "OBSERVED_NON_SUCCESS_CHECKS"
    elif checks is None:
        next_step = "Restore check-source visibility before any merge eligibility decision."
        status = "CHECK_SOURCE_UNKNOWN"
    else:
        next_step = "Verify all required CI, independent review, open threads, protection and live owner claims."
        status = "HUMAN_GATES_UNVERIFIED"

    # GitHub may recompute mergeability asynchronously. Keep its hint separate
    # from stable PR identity and expose drift as UNKNOWN instead of hiding it.
    mergeability_hint_stable = (
        first.get("mergeable_hint") == second.get("mergeable_hint")
    )
    base_tip_matches_default = (
        first["base_sha"] == default_branch_sha
        if first["target_branch"] == default_branch and
        default_branch is not None and default_branch_sha_stable else None
    )
    pr_base_sync = (
        "current" if base_tip_matches_default is True else
        "stale" if base_tip_matches_default is False else "unknown"
    )
    report = {
        "schema_version": 1, "policy": "zsentinel-exact-pr-preflight-v1",
        "url": pr_url, "observed_at_utc": utc(),
        "observed": first, "status": status,
        "source_stable_between_reads": True,
        "mergeability_hint_stable": mergeability_hint_stable,
        "repository_default_branch": default_branch,
        "repository_default_branch_stable": default_branch_stable,
        "repository_default_branch_sha": default_branch_sha,
        "repository_default_branch_sha_stable": default_branch_sha_stable,
        "branch_protection_observation": branch_protection,
        "branch_protection_config_stable": branch_protection_stable,
        "branch_protection_observed_at_utc": utc() if branch_protection_stable else None,
        "base_tip_matches_default": base_tip_matches_default,
        "base_head_history": history_comparison,
        "merge_policy_input_hint": {
            "pr_base_sync": pr_base_sync,
            "pr_history_sync": (
                history_comparison["status"] if history_comparison is not None
                else "unknown"
            ),
            "ahead_by": history_comparison.get("ahead_by") if history_comparison else None,
            "behind_by": history_comparison.get("behind_by") if history_comparison else None,
            "comparison_base_sha": comparison_base_sha,
            "comparison_head_sha": first["exact_head_sha"],
            "source_stable_between_reads": True,
            "default_branch_tip_stable": default_branch_sha_stable,
            "advisory_only": True,
            "merge_authorized": False,
        },
        "target_is_default_branch": (
            first["target_branch"] == default_branch
            if default_branch is not None and default_branch_stable else None
        ),
        "secondary_source_gaps": missing, "sampled_check_runs": checks,
        "sampled_reviews": reviews, "get_attempts": attempted,
        "operator_gates": _gate_cards(
            first, checks, reviews, True, default_branch, default_branch_stable,
            default_branch_sha, default_branch_sha_stable,
            base_tip_matches_default,
            mergeability_hint_stable=mergeability_hint_stable,
            branch_protection=branch_protection,
            branch_protection_stable=branch_protection_stable,
        ),
        "next_human_step": next_step,
        "required_ci_verified": False, "independent_review_verified": False,
        "branch_protection_verified": False, "active_claim_verified": False,
        "owner_authorized": False, "merge_authorized": False,
        "merge_executed": False, "deployed": False, "advisory_only": True,
    }
    report["review_request_followup"] = _review_followup(
        report.get("observed"), report.get("sampled_reviews"),
        report["source_stable_between_reads"],
    )
    report["parallel_owner_actions"] = _parallel_owner_actions(report)
    return report


def _unknown_pr(pr_url, first, gaps, attempted, *, default_branch=None,
                default_branch_sha=None):
    """Do not retain a supposedly stable PR HEAD after provider uncertainty."""
    report = {
        "schema_version": 1, "policy": "zsentinel-exact-pr-preflight-v1",
        "url": pr_url, "observed_at_utc": utc(),
        "observed": None, "unconfirmed_first_head": first["exact_head_sha"],
        "status": "SOURCE_UNKNOWN", "source_stable_between_reads": False,
        "repository_default_branch": None,
        "unconfirmed_default_branch": default_branch,
        "repository_default_branch_stable": False,
        "repository_default_branch_sha": None,
        "unconfirmed_default_branch_sha": default_branch_sha,
        "repository_default_branch_sha_stable": False,
        "branch_protection_observation": None,
        "branch_protection_config_stable": False,
        "branch_protection_observed_at_utc": None,
        "base_head_history": None,
        "merge_policy_input_hint": {
            "pr_base_sync": "unknown",
            "pr_history_sync": "unknown",
            "ahead_by": None,
            "behind_by": None,
            "comparison_base_sha": None,
            "comparison_head_sha": None,
            "source_stable_between_reads": False,
            "default_branch_tip_stable": False,
            "advisory_only": True,
            "merge_authorized": False,
        },
        "base_tip_matches_default": None,
        "target_is_default_branch": None,
        "secondary_source_gaps": gaps, "sampled_check_runs": None,
        "sampled_reviews": None, "get_attempts": attempted,
        "operator_gates": _gate_cards(
            None, None, None, False, default_branch, False,
            default_branch_sha, False, None,
            branch_protection=None, branch_protection_stable=False,
        ),
        "next_human_step": "Recover source admission and re-read the exact PR; do not merge.",
        "required_ci_verified": False, "independent_review_verified": False,
        "branch_protection_verified": False, "active_claim_verified": False,
        "owner_authorized": False, "merge_authorized": False,
        "merge_executed": False, "deployed": False, "advisory_only": True,
    }
    report["review_request_followup"] = _review_followup(
        report.get("observed"), report.get("sampled_reviews"),
        report["source_stable_between_reads"],
    )
    report["parallel_owner_actions"] = _parallel_owner_actions(report)
    return report


def as_markdown(report):
    observed = report.get("observed") or {}
    checks = report.get("sampled_check_runs") or {}
    reviews = report.get("sampled_reviews") or {}
    lines = [
        "# zSentinel — live exact-PR owner preflight", "",
        "**HOLD: observation only; NOT safe-to-merge approval.**", "",
        "- Pull request: " + report["url"],
        "- GitHub review conversation: " + report["url"],
        "- Changed files for owner review: " + report["url"] + "/files",
        "- Source observed (UTC): " + report["observed_at_utc"],
        "- Source status: " + report["status"],
        "- Exact HEAD (only after stable re-read): " +
        (observed.get("exact_head_sha") or "UNKNOWN"),
        "- Open exact-HEAD GitHub checks (navigation only): " +
        (observed.get("exact_head_checks_url") or "UNKNOWN"
         if report.get("source_stable_between_reads") else "UNKNOWN"),
        "- GitHub merged hint: " + str(observed.get("reported_merged", "UNKNOWN")),
        "- Base branch: " + str(observed.get("target_branch", "UNKNOWN")),
        "- PR base SHA: " + str(observed.get("base_sha", "UNKNOWN")),
        "- Repository default branch: " + str(report.get("repository_default_branch") or "UNKNOWN"),
        "- Repository default branch SHA: " + str(report.get("repository_default_branch_sha") or "UNKNOWN"),
        "- Default branch stable across reads: " + str(report.get("repository_default_branch_stable", False)),
        "- Default branch tip stable across reads: " + str(report.get("repository_default_branch_sha_stable", False)),
        "- Base tip matches current default: " + _known(report.get("base_tip_matches_default")),
        "- Exact base/head GitHub compare for owner inspection: " +
        ((report.get("base_head_history") or {}).get("browser_url") or "UNKNOWN"),
        "- Compare link is navigation only; it does not prove merge readiness.",
        "- Recovery-policy input hint evidence.pr_base_sync: " +
        str((report.get("merge_policy_input_hint") or {}).get("pr_base_sync", "unknown")),
        "- Recovery-policy input hint evidence.pr_history_sync: " +
        str((report.get("merge_policy_input_hint") or {}).get("pr_history_sync", "unknown")) +
        " (ahead_by=" + _known((report.get("merge_policy_input_hint") or {}).get("ahead_by")) +
        ", behind_by=" + _known((report.get("merge_policy_input_hint") or {}).get("behind_by")) + ")",
        "- Base is current default: " + _known(report.get("target_is_default_branch")),
        "- Branch protection configuration stable: " + str(report.get("branch_protection_config_stable", False)),
        "- Branch protection configuration (not gate satisfaction): " +
        json.dumps(report.get("branch_protection_observation") or "UNKNOWN", sort_keys=True),
        "- Branch protection verified/satisfied: false (this observer never grants merge authority)",
        "- Sampled checks: " + str(checks.get("sampled_count", "UNKNOWN")) +
        " of " + str(checks.get("provider_total_count", "UNKNOWN")),
        "- Sampled review states (all observed commits): " +
        json.dumps(reviews.get("observed_review_states", {}), sort_keys=True),
        "- Pending requested individual reviewers (not approvals): " +
        _known(observed.get("pending_reviewer_requests") if report.get("source_stable_between_reads") else None),
        "- Pending requested team reviews (not approvals): " +
        _known(observed.get("pending_team_review_requests") if report.get("source_stable_between_reads") else None),
        "- Reviews bound to exact HEAD: " + str(reviews.get("exact_head_review_count", "UNKNOWN")),
        "- Exact-HEAD review states: " +
        json.dumps(reviews.get("observed_review_states_exact_head", "UNKNOWN"), sort_keys=True),
        "- Reviews on other HEADs: " + str(reviews.get("other_head_review_count", "UNKNOWN")),
        "- Reviews without valid commit binding: " + str(reviews.get("unbound_review_count", "UNKNOWN")),
        "- Review records with unknown/missing state: " + str(reviews.get("unknown_review_state_count", "UNKNOWN")),
        "- Exact-HEAD review coverage: " + str(reviews.get("exact_head_review_coverage", "UNKNOWN")),
        "- Independent review verdict: UNKNOWN (counts are not approval authority)",
        "- Reviewer follow-up (human only): " + report["review_request_followup"]["state"] +
        " — " + report["review_request_followup"]["next"],
        "- GitHub GET attempts: " + str(report["get_attempts"]),
        "", "## Operator next step", "", report["next_human_step"], "",
        "## Owner gate checklist — observation is not authorization", "",
    ]
    lines.extend("- " + item["gate"] + ": " + item["state"] + " — " + item["next"]
                 for item in report["operator_gates"])
    lines.extend(["", "## Parallel owner actions — none executed", ""])
    for card in report.get("parallel_owner_actions", []):
        lines.extend(("- " + card["code"] + " · " + card["problem"],
                      "  Action: " + card["owner_action"],
                      "  Status: " + card["status"] + " · " + card["evidence"]))
    lines.extend(["", "## Missing sources", ""])
    lines.extend("- " + entry for entry in report["secondary_source_gaps"])
    if not report["secondary_source_gaps"]:
        lines.append("- None observed in bounded samples; required-gate completeness remains UNKNOWN.")
    lines.extend([
        "", "## Observed exact-head check concerns", "",
    ])
    for label, field in (
        ("Potentially blocking non-success", "sampled_blocking_checks"),
        ("Neutral/skipped non-success", "sampled_non_success_checks"),
        ("Pending", "sampled_pending_checks"),
    ):
        for entry in checks.get(field, []):
            check_label = "`" + entry["name"].replace("`", "'") + "`"
            check_url = entry.get("url")
            if isinstance(check_url, str) and check_url.startswith("https://github.com/"):
                check_label = "[" + check_label + "](" + check_url + ")"
            lines.append("- " + label + ": " + check_label +
                         " (" + str(entry.get("conclusion") or entry.get("status")) + ")")
    lines.extend([
        "", "**Always recheck:** required exact-head CI, independent reviewer,",
        "review threads, branch protection, live claim ownership and owner permission.",
        "This preflight never merges, retries, claims, deploys or restarts.", "",
    ])
    return "\n".join(lines)


def as_html(report):
    esc = lambda value: html.escape(str(value), quote=True)
    observed = report.get("observed") or {}
    checks = report.get("sampled_check_runs") or {}
    reviews = report.get("sampled_reviews") or {}
    details = [
        ("Source state", report["status"]),
        ("Observed at", report["observed_at_utc"]),
        ("Stable exact HEAD", observed.get("exact_head_sha", "UNKNOWN")),
        ("Base", observed.get("target_branch", "UNKNOWN")),
        ("PR base SHA", observed.get("base_sha", "UNKNOWN")),
        ("Repository default branch", report.get("repository_default_branch") or "UNKNOWN"),
        ("Repository default branch SHA", report.get("repository_default_branch_sha") or "UNKNOWN"),
        ("Default branch stable across reads", report.get("repository_default_branch_stable", False)),
        ("Default branch tip stable across reads", report.get("repository_default_branch_sha_stable", False)),
        ("Base tip matches current default", _known(report.get("base_tip_matches_default"))),
        ("Exact base/head GitHub compare navigation",
         (report.get("base_head_history") or {}).get("browser_url") or "UNKNOWN"),
        ("Recovery-policy input hint evidence.pr_base_sync",
         (report.get("merge_policy_input_hint") or {}).get("pr_base_sync", "unknown")),
        ("Recovery-policy input hint evidence.pr_history_sync",
         str((report.get("merge_policy_input_hint") or {}).get("pr_history_sync", "unknown")) +
         " (ahead_by=" + _known((report.get("merge_policy_input_hint") or {}).get("ahead_by")) +
         ", behind_by=" + _known((report.get("merge_policy_input_hint") or {}).get("behind_by")) + ")"),
        ("Base is current default", _known(report.get("target_is_default_branch"))),
        ("Branch protection configuration stable", report.get("branch_protection_config_stable", False)),
        ("Branch protection configuration (not gate satisfaction)",
         json.dumps(report.get("branch_protection_observation") or "UNKNOWN", sort_keys=True)),
        ("Branch protection verified/satisfied", False),
        ("Reported merged", observed.get("reported_merged", "UNKNOWN")),
        ("Check sample", str(checks.get("sampled_count", "UNKNOWN")) + " / " +
         str(checks.get("provider_total_count", "UNKNOWN"))),
        ("Review sample, all observed commits", json.dumps(reviews.get("observed_review_states", {}), sort_keys=True)),
        ("Pending requested individual reviewers (not approvals)",
         _known(observed.get("pending_reviewer_requests") if report.get("source_stable_between_reads") else None)),
        ("Pending requested team reviews (not approvals)",
         _known(observed.get("pending_team_review_requests") if report.get("source_stable_between_reads") else None)),
        ("Reviews bound to exact HEAD", reviews.get("exact_head_review_count", "UNKNOWN")),
        ("Exact-HEAD review states", json.dumps(reviews.get("observed_review_states_exact_head", "UNKNOWN"), sort_keys=True)),
        ("Reviews on other HEADs", reviews.get("other_head_review_count", "UNKNOWN")),
        ("Reviews without valid commit binding", reviews.get("unbound_review_count", "UNKNOWN")),
        ("Review records with unknown/missing state", reviews.get("unknown_review_state_count", "UNKNOWN")),
        ("Exact-HEAD review coverage", reviews.get("exact_head_review_coverage", "UNKNOWN")),
        ("Independent review verdict", "UNKNOWN — sampled counts are not approval authority"),
        ("Reviewer follow-up", report["review_request_followup"]["state"] +
         " — " + report["review_request_followup"]["next"]),
    ]
    parts = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'\">",
        "<title>zSentinel exact PR preflight</title>",
        "<style>body{max-width:880px;margin:2rem auto;padding:0 1rem;font:16px/1.5 system-ui;color:#152331;background:#f7f9fc}section{background:white;border:1px solid #cad4df;border-left:5px solid #a75b22;border-radius:.5rem;padding:1rem;margin:1rem 0}dt{font-weight:700}dd{margin:0 0 .7rem;overflow-wrap:anywhere}a{color:#17569c}li{margin:.4rem 0}</style>",
        "</head><body><h1>zSentinel — exact PR preflight</h1>",
        "<p><strong>HOLD · not a merge authorization.</strong> This report never executes an action.</p>",
        "<section><h2>Live PR identity</h2><p><a href='" + esc(report["url"]) +
        "'>Open current PR</a></p><dl>",
    ]
    if report.get("source_stable_between_reads") and observed.get("exact_head_checks_url"):
        parts.append("<p><a href='" + esc(observed["exact_head_checks_url"]) +
                     "'>Inspect GitHub checks for this exact HEAD (read-only)</a></p>")
    compare_url = (report.get("base_head_history") or {}).get("browser_url")
    if compare_url and report.get("source_stable_between_reads"):
        parts.append("<p><a href='" + esc(compare_url) +
                     "'>Inspect exact base/HEAD difference in GitHub (navigation only)</a></p>")
    parts.extend("<dt>" + esc(k) + "</dt><dd>" + esc(v) + "</dd>" for k, v in details)
    followup = report["review_request_followup"]
    parts.extend([
        "</dl></section><section><h2>Review coordination (read-only)</h2>",
        "<p><strong>" + esc(followup["state"]) + "</strong> — " +
        esc(followup["next"]) + "</p>",
        "<p><a href='" + esc(report["url"]) + "'>Open PR conversation / reviewers</a> · ",
        "<a href='" + esc(report["url"] + "/files") +
        "'>Inspect source changes</a></p>",
        "<p>These are current-request observations only, not approval; "
        "required exact-head checks, independent review and branch protection "
        "must still be confirmed before GitHub merge.</p>",
        "</section><section><h2>Operator action</h2><p>",
                  esc(report["next_human_step"]), "</p></section>",
                  "<section><h2>Owner gate checklist</h2><ul>"])
    for gate in report["operator_gates"]:
        parts.append("<li><strong>" + esc(gate["gate"]) + " — " +
                     esc(gate["state"]) + "</strong>: " + esc(gate["next"]) + "</li>")
    parts.extend(["</ul></section><section><h2>Parallel owner actions — not executed</h2><ul>"])
    for card in report.get("parallel_owner_actions", []):
        parts.append("<li><strong>" + esc(card["problem"]) + "</strong><br>" +
                     esc(card["owner_action"]) + " <small>(" + esc(card["code"]) +
                     ") · " + esc(card["status"]) + "</small></li>")
    parts.extend(["</ul></section><section><h2>Source gaps and sampled checks</h2><ul>"])
    for gap in report["secondary_source_gaps"]:
        parts.append("<li>" + esc(gap) + "</li>")
    for key in ("sampled_blocking_checks", "sampled_non_success_checks", "sampled_pending_checks"):
        for item in checks.get(key, []):
            label = esc(item["name"]) + " — " + esc(item.get("conclusion") or item.get("status"))
            check_url = item.get("url")
            if isinstance(check_url, str) and check_url.startswith("https://github.com/"):
                label = "<a href='" + esc(check_url) + "'>" + label + "</a>"
            parts.append("<li>" + label + "</li>")
    parts.extend(["</ul><p>Unknown required CI, branch protection, independent review, claims and owner authorization always remain HOLD.</p></section>",
                  "<footer>No retry, merge, deploy, queue mutation or runner restart occurred.</footer>",
                  "</body></html>\n"])
    return "".join(parts)

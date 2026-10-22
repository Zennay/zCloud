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
    _limited_json, _pr_summary, _review_summary, utc,
)


def _known(value):
    """Render missing source facts as UNKNOWN without hiding a real False."""
    return "UNKNOWN" if value is None else str(value)


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
                default_branch_sha_stable=False, base_tip_matches_default=None):
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
    return [
        {"gate": "exact_head", "state": state,
         "next": "Reconfirm current exact SHA at the time of authorized merge."},
        {"gate": "pr_open_non_draft_on_default_branch", "state": branch_gate,
         "next": "Target the observed repository default branch (" +
                 (default_branch or "UNKNOWN") +
                 ") and independently confirm intended release scope."},
        {"gate": "base_tip_current", "state": base_tip_gate,
         "next": "Compare the PR base SHA with the stable default-branch tip; if stale, update the existing branch and resolve conflicts without force or gate bypass."},
        {"gate": "head_check_sample", "state": sample,
         "next": "Resolve visible failures and pending jobs; independently determine mandatory checks."},
        {"gate": "all_required_ci", "state": "UNKNOWN",
         "next": "Confirm every branch-required check on this exact SHA."},
        {"gate": "independent_review_and_threads", "state": "UNKNOWN",
         "next": "Confirm latest independent approval and zero unresolved review threads.",
         "sample_available": reviews is not None},
        {"gate": "branch_protection", "state": "UNKNOWN",
         "next": "Confirm protection without bypass, force or disabled checks."},
        {"gate": "claim_and_owner", "state": "UNKNOWN",
         "next": "Check active worker/Notion claims and get explicit owner authorization."},
        {"gate": "default_branch_merge_and_live", "state": "NOT_EXECUTED",
         "next": "After authorized merge verify merged=true, exact current default-branch SHA and separate live deploy."},
    ]


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
            "evidence": evidence, "execution_authorized": False,
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
    if observed.get("observed_draft") is True:
        add("finish_existing_draft",
            "The PR is still draft.",
            "Ask its current owner to mark ready only after review preparation.",
            "DRAFT")
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
        add("request_independent_review",
            "No submitted reviews appeared in the sampled PR review source.",
            "Request an independent review; verify approval, head relevance and threads.",
            "ZERO_REVIEWS_IN_SAMPLE")
    elif reviews.get("exact_head_review_coverage") != "OBSERVED":
        add("reconcile_partial_review_coverage",
            "Some sampled review entries are unbound, unknown or truncated.",
            "Inspect effective reviewer decisions and unresolved threads on GitHub.",
            "PARTIAL_REVIEW_SAMPLE")
    add("verify_authority_and_protection",
        "Branch protection, current claims and release-owner permission remain unverified.",
        "Check all required CI, independent review, protection and current ownership before merge.",
        "HUMAN_GATES_UNKNOWN")
    return cards[:8]


def observe_exact_pr(pr_url, *, token=None):
    """At most eight bounded GETs; pin PR, default-branch name and tip to stable reads."""
    match = REPO_URL.fullmatch(pr_url) if isinstance(pr_url, str) else None
    if match is None or len(pr_url) > 250:
        raise GitHubReadError("provide one canonical Zennay GitHub pull-request URL")
    full_name = match[1] + "/" + match[2]
    number = int(match[3])
    if not 1 <= number <= 100000000:
        raise GitHubReadError("PR number is out of bounds")
    api = "/repos/" + quote(match[1], safe="") + "/" + quote(match[2], safe="")
    path = api + "/pulls/" + str(number)
    first = _pr_summary(_limited_json(path, token=token), full_name, number)
    checks = None
    reviews = None
    missing = []
    attempted = 1
    default_branch = None
    default_branch_stable = False
    default_branch_sha = None
    default_branch_sha_stable = False
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
        second = _pr_summary(_limited_json(path, token=token), full_name, number)
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
    )
    if any(first[k] != second[k] for k in stable_keys):
        return _unknown_pr(
            pr_url, first,
            ["PR state or HEAD moved between source reads; re-observe"], attempted,
        )

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

    report = {
        "schema_version": 1, "policy": "zsentinel-exact-pr-preflight-v1",
        "url": pr_url, "observed_at_utc": utc(),
        "observed": first, "status": status,
        "source_stable_between_reads": True,
        "repository_default_branch": default_branch,
        "repository_default_branch_stable": default_branch_stable,
        "repository_default_branch_sha": default_branch_sha,
        "repository_default_branch_sha_stable": default_branch_sha_stable,
        "base_tip_matches_default": (
            first["base_sha"] == default_branch_sha
            if first["target_branch"] == default_branch and
            default_branch is not None and default_branch_sha_stable else None
        ),
        "target_is_default_branch": (
            first["target_branch"] == default_branch
            if default_branch is not None and default_branch_stable else None
        ),
        "secondary_source_gaps": missing, "sampled_check_runs": checks,
        "sampled_reviews": reviews, "get_attempts": attempted,
        "operator_gates": _gate_cards(
            first, checks, reviews, True, default_branch, default_branch_stable,
            default_branch_sha, default_branch_sha_stable,
            first["base_sha"] == default_branch_sha
            if first["target_branch"] == default_branch and
            default_branch_sha is not None and default_branch_sha_stable else None,
        ),
        "next_human_step": next_step,
        "required_ci_verified": False, "independent_review_verified": False,
        "branch_protection_verified": False, "active_claim_verified": False,
        "owner_authorized": False, "merge_authorized": False,
        "merge_executed": False, "deployed": False, "advisory_only": True,
    }
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
        "base_tip_matches_default": None,
        "target_is_default_branch": None,
        "secondary_source_gaps": gaps, "sampled_check_runs": None,
        "sampled_reviews": None, "get_attempts": attempted,
        "operator_gates": _gate_cards(
            None, None, None, False, default_branch, False,
            default_branch_sha, False, None,
        ),
        "next_human_step": "Recover source admission and re-read the exact PR; do not merge.",
        "required_ci_verified": False, "independent_review_verified": False,
        "branch_protection_verified": False, "active_claim_verified": False,
        "owner_authorized": False, "merge_authorized": False,
        "merge_executed": False, "deployed": False, "advisory_only": True,
    }
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
        "- Source observed (UTC): " + report["observed_at_utc"],
        "- Source status: " + report["status"],
        "- Exact HEAD (only after stable re-read): " +
        (observed.get("exact_head_sha") or "UNKNOWN"),
        "- GitHub merged hint: " + str(observed.get("reported_merged", "UNKNOWN")),
        "- Base branch: " + str(observed.get("target_branch", "UNKNOWN")),
        "- PR base SHA: " + str(observed.get("base_sha", "UNKNOWN")),
        "- Repository default branch: " + str(report.get("repository_default_branch") or "UNKNOWN"),
        "- Repository default branch SHA: " + str(report.get("repository_default_branch_sha") or "UNKNOWN"),
        "- Default branch stable across reads: " + str(report.get("repository_default_branch_stable", False)),
        "- Default branch tip stable across reads: " + str(report.get("repository_default_branch_sha_stable", False)),
        "- Base tip matches current default: " + _known(report.get("base_tip_matches_default")),
        "- Base is current default: " + _known(report.get("target_is_default_branch")),
        "- Sampled checks: " + str(checks.get("sampled_count", "UNKNOWN")) +
        " of " + str(checks.get("provider_total_count", "UNKNOWN")),
        "- Sampled review states (all observed commits): " +
        json.dumps(reviews.get("observed_review_states", {}), sort_keys=True),
        "- Reviews bound to exact HEAD: " + str(reviews.get("exact_head_review_count", "UNKNOWN")),
        "- Exact-HEAD review states: " +
        json.dumps(reviews.get("observed_review_states_exact_head", "UNKNOWN"), sort_keys=True),
        "- Reviews on other HEADs: " + str(reviews.get("other_head_review_count", "UNKNOWN")),
        "- Reviews without valid commit binding: " + str(reviews.get("unbound_review_count", "UNKNOWN")),
        "- Review records with unknown/missing state: " + str(reviews.get("unknown_review_state_count", "UNKNOWN")),
        "- Exact-HEAD review coverage: " + str(reviews.get("exact_head_review_coverage", "UNKNOWN")),
        "- Independent review verdict: UNKNOWN (counts are not approval authority)",
        "- GitHub GET attempts: " + str(report["get_attempts"]),
        "", "## Operator next step", "", report["next_human_step"], "",
        "## Owner gate checklist — observation is not authorization", "",
    ]
    lines.extend("- " + item["gate"] + ": " + item["state"] + " — " + item["next"]
                 for item in report["operator_gates"])
    lines.extend(["", "## Parallel owner actions — none executed", ""])
    for card in report.get("parallel_owner_actions", []):
        lines.extend(("- " + card["code"] + " · " + card["problem"],
                      "  Action: " + card["owner_action"]))
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
        ("Base is current default", _known(report.get("target_is_default_branch"))),
        ("Reported merged", observed.get("reported_merged", "UNKNOWN")),
        ("Check sample", str(checks.get("sampled_count", "UNKNOWN")) + " / " +
         str(checks.get("provider_total_count", "UNKNOWN"))),
        ("Review sample, all observed commits", json.dumps(reviews.get("observed_review_states", {}), sort_keys=True)),
        ("Reviews bound to exact HEAD", reviews.get("exact_head_review_count", "UNKNOWN")),
        ("Exact-HEAD review states", json.dumps(reviews.get("observed_review_states_exact_head", "UNKNOWN"), sort_keys=True)),
        ("Reviews on other HEADs", reviews.get("other_head_review_count", "UNKNOWN")),
        ("Reviews without valid commit binding", reviews.get("unbound_review_count", "UNKNOWN")),
        ("Review records with unknown/missing state", reviews.get("unknown_review_state_count", "UNKNOWN")),
        ("Exact-HEAD review coverage", reviews.get("exact_head_review_coverage", "UNKNOWN")),
        ("Independent review verdict", "UNKNOWN — sampled counts are not approval authority"),
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
    parts.extend("<dt>" + esc(k) + "</dt><dd>" + esc(v) + "</dd>" for k, v in details)
    parts.extend(["</dl></section><section><h2>Operator action</h2><p>",
                  esc(report["next_human_step"]), "</p></section>",
                  "<section><h2>Owner gate checklist</h2><ul>"])
    for gate in report["operator_gates"]:
        parts.append("<li><strong>" + esc(gate["gate"]) + " — " +
                     esc(gate["state"]) + "</strong>: " + esc(gate["next"]) + "</li>")
    parts.extend(["</ul></section><section><h2>Parallel owner actions — not executed</h2><ul>"])
    for card in report.get("parallel_owner_actions", []):
        parts.append("<li><strong>" + esc(card["problem"]) + "</strong><br>" +
                     esc(card["owner_action"]) + " <small>(" + esc(card["code"]) +
                     ")</small></li>")
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

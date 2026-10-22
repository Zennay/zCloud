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


def _gate_cards(head, checks, reviews, stable):
    """Operator workorders; read-only samples never satisfy release gates."""
    state = ("OBSERVED" if stable else "UNKNOWN")
    sample = ("UNKNOWN" if checks is None else
              "OBSERVED_FAILURE" if checks["sampled_blocking_checks"] else
              "OBSERVED_PENDING" if checks["sampled_pending_checks"] else
              "SAMPLE_ONLY")
    return [
        {"gate": "exact_head", "state": state,
         "next": "Reconfirm current exact SHA at the time of authorized merge."},
        {"gate": "pr_open_non_draft_on_main",
         "state": ("OBSERVED" if stable and head and
                   head["observed_state"] == "open" and
                   not head["observed_draft"] and
                   not head["reported_merged"] and
                   head["target_branch"] == "main" else "HOLD"),
         "next": "Check the actual default branch and intended PR release target."},
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
        {"gate": "main_merge_and_live", "state": "NOT_EXECUTED",
         "next": "After authorized merge verify merged=true, exact main SHA and separate live deploy."},
    ]


def observe_exact_pr(pr_url, *, token=None):
    """Four GETs maximum; re-read the PR to pin secondary hints to one HEAD."""
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
    try:
        attempted += 1
        checks = _checks_summary(_limited_json(
            api + "/commits/" + first["exact_head_sha"] +
            "/check-runs?per_page=" + str(MAX_LIST_PAGE), token=token,
        ), first["exact_head_sha"], full_name)
    except GitHubReadError as exc:
        missing.append("check-runs: " + str(exc))
        if "authentication, quota or provider admission" in str(exc):
            return _unknown_pr(pr_url, first, missing, attempted)

    try:
        attempted += 1
        reviews = _review_summary(_limited_json(
            path + "/reviews?per_page=" + str(MAX_LIST_PAGE), token=token,
        ))
    except GitHubReadError as exc:
        missing.append("reviews: " + str(exc))
        if "authentication, quota or provider admission" in str(exc):
            return _unknown_pr(pr_url, first, missing, attempted)

    attempted += 1
    second = _pr_summary(_limited_json(path, token=token), full_name, number)
    stable_keys = (
        "number", "observed_state", "observed_draft", "reported_merged",
        "target_branch", "exact_head_sha",
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
    elif first["target_branch"] != "main":
        next_step = "Confirm the intended default branch and release owner before integrating."
        status = "BASE_NOT_MAIN"
    elif first["observed_draft"]:
        next_step = "Finish the existing draft with its owner; keep independent review and CI gates."
        status = "DRAFT"
    elif checks is not None and checks["sampled_blocking_checks"]:
        next_step = "Repair the observed exact-head failure without bypassing required checks."
        status = "OBSERVED_FAILED_CHECKS"
    elif checks is not None and checks["sampled_pending_checks"]:
        next_step = "Wait for exact-head checks or resolve their actual runner/admission dependency."
        status = "OBSERVED_PENDING_CHECKS"
    elif checks is None:
        next_step = "Restore check-source visibility before any merge eligibility decision."
        status = "CHECK_SOURCE_UNKNOWN"
    else:
        next_step = "Verify all required CI, independent review, open threads, protection and live owner claims."
        status = "HUMAN_GATES_UNVERIFIED"

    return {
        "schema_version": 1, "policy": "zsentinel-exact-pr-preflight-v1",
        "url": pr_url, "observed_at_utc": utc(),
        "observed": first, "status": status,
        "source_stable_between_reads": True,
        "secondary_source_gaps": missing, "sampled_check_runs": checks,
        "sampled_reviews": reviews, "get_attempts": attempted,
        "operator_gates": _gate_cards(first, checks, reviews, True),
        "next_human_step": next_step,
        "required_ci_verified": False, "independent_review_verified": False,
        "branch_protection_verified": False, "active_claim_verified": False,
        "owner_authorized": False, "merge_authorized": False,
        "merge_executed": False, "deployed": False, "advisory_only": True,
    }


def _unknown_pr(pr_url, first, gaps, attempted):
    """Do not retain a supposedly stable PR HEAD after provider uncertainty."""
    return {
        "schema_version": 1, "policy": "zsentinel-exact-pr-preflight-v1",
        "url": pr_url, "observed_at_utc": utc(),
        "observed": None, "unconfirmed_first_head": first["exact_head_sha"],
        "status": "SOURCE_UNKNOWN", "source_stable_between_reads": False,
        "secondary_source_gaps": gaps, "sampled_check_runs": None,
        "sampled_reviews": None, "get_attempts": attempted,
        "operator_gates": _gate_cards(None, None, None, False),
        "next_human_step": "Recover source admission and re-read the exact PR; do not merge.",
        "required_ci_verified": False, "independent_review_verified": False,
        "branch_protection_verified": False, "active_claim_verified": False,
        "owner_authorized": False, "merge_authorized": False,
        "merge_executed": False, "deployed": False, "advisory_only": True,
    }


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
        "- Sampled checks: " + str(checks.get("sampled_count", "UNKNOWN")) +
        " of " + str(checks.get("provider_total_count", "UNKNOWN")),
        "- Sampled review states: " +
        json.dumps(reviews.get("observed_review_states", {}), sort_keys=True),
        "- GitHub GET attempts: " + str(report["get_attempts"]),
        "", "## Operator next step", "", report["next_human_step"], "",
        "## Missing source segments", "",
    ]
    lines.extend("- " + item["gate"] + ": " + item["state"] + " — " + item["next"]
                 for item in report["operator_gates"])
    lines.extend(["", "## Missing sources", ""])
    lines.extend("- " + entry for entry in report["secondary_source_gaps"])
    if not report["secondary_source_gaps"]:
        lines.append("- None observed in bounded samples; required-gate completeness remains UNKNOWN.")
    lines.extend([
        "", "## Observed exact-head check concerns", "",
    ])
    for label, field in (
        ("Failure", "sampled_blocking_checks"), ("Pending", "sampled_pending_checks"),
    ):
        for entry in checks.get(field, []):
            lines.append("- " + label + ": " + entry["name"] +
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
        ("Reported merged", observed.get("reported_merged", "UNKNOWN")),
        ("Check sample", str(checks.get("sampled_count", "UNKNOWN")) + " / " +
         str(checks.get("provider_total_count", "UNKNOWN"))),
        ("Review sample", json.dumps(reviews.get("observed_review_states", {}), sort_keys=True)),
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
                  "<section><h2>Source gaps and sampled checks</h2><ul>"])
    for gate in report["operator_gates"]:
        parts.append("<li><strong>" + esc(gate["gate"]) + " — " +
                     esc(gate["state"]) + "</strong>: " + esc(gate["next"]) + "</li>")
    for gap in report["secondary_source_gaps"]:
        parts.append("<li>" + esc(gap) + "</li>")
    for key in ("sampled_blocking_checks", "sampled_pending_checks"):
        for item in checks.get(key, []):
            parts.append("<li>" + esc(item["name"]) + " — " +
                         esc(item.get("conclusion") or item.get("status")) + "</li>")
    parts.extend(["</ul><p>Unknown required CI, branch protection, independent review, claims and owner authorization always remain HOLD.</p></section>",
                  "<footer>No retry, merge, deploy, queue mutation or runner restart occurred.</footer>",
                  "</body></html>\n"])
    return "".join(parts)

#!/usr/bin/env python3
"""Bounded live GitHub PR observations for the source-linked zSentinel owner desk.

This read-only adapter observes at most three historically referenced
candidate PRs for *one* exact issue. It deliberately cannot authorize a
merge, claim, rerun or deployment. GitHub API failures are UNKNOWN, never
a missing PR, available owner or green required CI.
"""
from __future__ import annotations

import html
import json
import os
import re
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, HTTPRedirectHandler, build_opener

UTC = timezone.utc
SHA = re.compile("^[a-f0-9]{40}$")
REPO_URL = re.compile(
    "^https://github[.]com/(Zennay)/([A-Za-z0-9_.-]{1,100})/pull/([1-9][0-9]*)$"
)
MAX_BODY_BYTES = 160 * 1024
MAX_CANDIDATES = 3
MAX_LIST_PAGE = 30


class GitHubReadError(ValueError):
    """GitHub public/private source was not usable for a non-authorizing view."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise GitHubReadError("GitHub API redirect refused")


def utc():
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _limited_json(path, *, token=None):
    """One fixed-host HTTPS GitHub GET, no redirects, strict byte cap."""
    if not path.startswith("/repos/Zennay/") or (
        any(char in path for char in ("#", "\\", "\n", "\r", "\t"))
    ):
        raise GitHubReadError("invalid bounded GitHub API path")
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "zsentinel-readonly-pr-owner-desk",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = "Bearer " + token
    req = Request("https://api.github.com" + path, headers=headers, method="GET")
    try:
        with build_opener(NoRedirect).open(req, timeout=7) as response:
            if response.status != 200:
                raise GitHubReadError("GitHub API returned non-200 status")
            data = response.read(MAX_BODY_BYTES + 1)
            if len(data) > MAX_BODY_BYTES:
                raise GitHubReadError("bounded GitHub response size exceeded")
            return json.loads(data.decode("utf-8"))
    except HTTPError as exc:
        # Never print provider error bodies: a private error could echo secrets.
        if exc.code in (401, 403, 429):
            raise GitHubReadError(
                "GitHub authentication, quota or provider admission unavailable"
            ) from exc
        if exc.code == 404:
            raise GitHubReadError(
                "GitHub PR inaccessible or not found; do not infer deletion"
            ) from exc
        raise GitHubReadError(
            "GitHub read unavailable (HTTP " + str(exc.code) + ")"
        ) from exc
    except (URLError, TimeoutError, UnicodeError, json.JSONDecodeError) as exc:
        raise GitHubReadError("GitHub read unavailable or malformed") from exc


def _pr_summary(payload, full_name, number):
    if not isinstance(payload, dict) or (
        type(payload.get("number")) is not int or payload["number"] != number
    ):
        raise GitHubReadError("PR response has inconsistent identity")
    base, head = payload.get("base"), payload.get("head")
    if not isinstance(base, dict) or not isinstance(head, dict):
        raise GitHubReadError("PR response omitted base/head")
    repo = base.get("repo")
    if not isinstance(repo, dict) or repo.get("full_name") != full_name:
        raise GitHubReadError("PR response is from a different repository")
    sha = head.get("sha")
    if not isinstance(sha, str) or not SHA.fullmatch(sha):
        raise GitHubReadError("PR HEAD SHA missing or invalid")
    state, draft, merged = payload.get("state"), payload.get("draft"), payload.get("merged")
    if state not in ("open", "closed") or type(draft) is not bool or type(merged) is not bool:
        raise GitHubReadError("PR state incomplete or inconsistent")
    base_ref = base.get("ref")
    if not isinstance(base_ref, str) or len(base_ref) > 120:
        raise GitHubReadError("PR target branch invalid")
    return {
        "number": number, "url": "https://github.com/" + full_name + "/pull/" + str(number),
        "observed_state": state, "observed_draft": draft,
        "reported_merged": merged, "target_branch": base_ref,
        "exact_head_sha": sha, "head_verified_by_live_get": True,
        "mergeable_hint": payload.get("mergeable") if type(payload.get("mergeable")) is bool else None,
        "updated_at_source": payload.get("updated_at") if isinstance(payload.get("updated_at"), str) else None,
    }


def _checks_summary(payload, sha):
    """A bounded incomplete HEAD-scoped observation, never a required-CI verdict."""
    if not isinstance(payload, dict) or type(payload.get("total_count")) is not int:
        raise GitHubReadError("check-run response missing total count")
    rows = payload.get("check_runs")
    count = payload["total_count"]
    if not isinstance(rows, list) or len(rows) > MAX_LIST_PAGE or count < len(rows):
        raise GitHubReadError("check-run sample invalid")
    sums = {"completed_success": 0, "completed_other": 0, "not_completed": 0}
    observed_names = []
    for row in rows:
        if not isinstance(row, dict):
            raise GitHubReadError("invalid check-run row")
        source_sha = row.get("head_sha")
        if source_sha is not None and source_sha != sha:
            raise GitHubReadError("check-run does not belong to observed exact HEAD")
        status = row.get("status")
        if status == "completed":
            sums["completed_success" if row.get("conclusion") == "success"
                 else "completed_other"] += 1
        else:
            sums["not_completed"] += 1
        name = row.get("name")
        if isinstance(name, str) and name[:120] not in observed_names:
            observed_names.append(name[:120])
    return {
        "sampled_count": len(rows), "provider_total_count": count,
        "sample_incomplete": count > len(rows),
        "sample_status_counts": sums, "sample_names": observed_names[:12],
        "required_checks_identified": False,
        "required_ci_verdict": "UNKNOWN",
    }


def _review_summary(rows):
    if not isinstance(rows, list) or len(rows) > MAX_LIST_PAGE:
        raise GitHubReadError("bounded PR review sample invalid")
    states = {}
    for row in rows:
        if not isinstance(row, dict):
            raise GitHubReadError("invalid review entry")
        state = row.get("state")
        if isinstance(state, str) and state in (
            "APPROVED", "CHANGES_REQUESTED", "COMMENTED", "DISMISSED", "PENDING"
        ):
            states[state] = states.get(state, 0) + 1
    return {
        "sampled_count": len(rows), "may_be_truncated": len(rows) == MAX_LIST_PAGE,
        "observed_review_states": states,
        "independent_review_verdict": "UNKNOWN",
    }


def observe_candidate_prs(issue, *, token=None):
    """Read exactly an admitted issue's referenced PRs; leave each result untrusted."""
    candidates = issue.get("candidate_prs", [])
    if not isinstance(candidates, list) or len(candidates) > MAX_CANDIDATES:
        raise GitHubReadError("unbounded or absent candidate source")
    if not candidates:
        raise GitHubReadError("issue has no sampled PR; refresh/discover existing work first")
    source_url = issue["source_urls"][0]
    expected_repo = source_url.rsplit("/issues/", 1)[0]
    observations = []
    provider_paused = False
    total_gets_attempted = 0
    for candidate in candidates:
        url = candidate["url"]
        match = REPO_URL.fullmatch(url)
        if not match or "https://github.com/" + match[1] + "/" + match[2] != expected_repo:
            raise GitHubReadError("candidate PR differs from original issue repository")
        number = int(match[3])
        full_name = match[1] + "/" + match[2]
        api = "/repos/" + quote(match[1], safe="") + "/" + quote(match[2], safe="")
        base = {
            "url": url, "number": number,
            "source_draft": candidate["draft_at_source"],
            "source_relationship": candidate["relationship_at_source"],
            "advisory_only": True, "action_authorized": False,
            "status": "UNKNOWN",
            "ownership_verified": False, "merge_authorized": False,
            "required_ci_verdict": "UNKNOWN",
            "independent_review_verdict": "UNKNOWN",
            "branch_protection_verdict": "UNKNOWN",
        }
        if provider_paused:
            base["reason"] = (
                "Skipped: GitHub provider admission unavailable earlier in this "
                "same bounded observation; no new request attempted"
            )
            base["status"] = "UNKNOWN_PROVIDER"
            base["next_human_step"] = "recheck_source_with_authorized_owner"
            observations.append(base)
            continue
        try:
            total_gets_attempted += 1
            before = _pr_summary(_limited_json(
                api + "/pulls/" + str(number), token=token,
            ), full_name, number)
            source_gaps = []
            checks = None
            reviews = None
            # Check runs and reviews are independent read-only sources. An
            # inaccessible secondary endpoint must not discard a stable PR
            # HEAD that GitHub can still confirm with another PR GET.
            try:
                total_gets_attempted += 1
                checks = _checks_summary(_limited_json(
                    api + "/commits/" + before["exact_head_sha"] +
                    "/check-runs?per_page=" + str(MAX_LIST_PAGE), token=token
                ), before["exact_head_sha"])
            except GitHubReadError as exc:
                source_gaps.append("check-runs: " + str(exc))
                if "authentication, quota or provider admission" in str(exc):
                    provider_paused = True
            if not provider_paused:
                try:
                    total_gets_attempted += 1
                    reviews = _review_summary(_limited_json(
                        api + "/pulls/" + str(number) +
                        "/reviews?per_page=" + str(MAX_LIST_PAGE), token=token
                    ))
                except GitHubReadError as exc:
                    source_gaps.append("reviews: " + str(exc))
                    if "authentication, quota or provider admission" in str(exc):
                        provider_paused = True
            if provider_paused:
                raise GitHubReadError(
                    "provider admission denied before PR HEAD could be rechecked"
                )
            total_gets_attempted += 1
            after = _pr_summary(_limited_json(
                api + "/pulls/" + str(number), token=token
            ), full_name, number)
            if (before["exact_head_sha"] != after["exact_head_sha"] or
                    before["observed_state"] != after["observed_state"] or
                    before["reported_merged"] != after["reported_merged"] or
                    before["observed_draft"] != after["observed_draft"]):
                raise GitHubReadError("PR changed during live read; no stable exact HEAD")
            base.update(before)
            base["observed_at_utc"] = utc()
            base["status"] = "LIVE_PARTIAL_READ"
            base["secondary_source_gaps"] = source_gaps
            if checks is not None:
                base["check_runs"] = checks
            if reviews is not None:
                base["reviews"] = reviews
            base["next_human_step"] = (
                "already_merged_verify_main_commit_and_deploy" if before["reported_merged"] else
                "not_open_refresh_project_and_issue" if before["observed_state"] != "open" else
                "finish_draft_with_current_owner" if before["observed_draft"] else
                "verify_protected_main_required_ci_independent_review_and_ownership"
            )
        except GitHubReadError as exc:
            base["status"] = "UNKNOWN_PROVIDER"
            base["reason"] = str(exc)
            if "authentication, quota or provider admission" in str(exc):
                provider_paused = True
            base["next_human_step"] = "recheck_source_with_authorized_owner"
        observations.append(base)
    return {
        "schema_version": 1, "policy": "zsentinel-live-pr-candidate-readonly-v1",
        "issue_project": issue["project_id"], "issue_id": issue["incident_id"],
        "original_issue_url": source_url,
        "observed_at_utc": utc(),
        "candidate_count": len(candidates),
        "source_candidate_count": issue["candidate_count"],
        "observations": observations, "sample_scope_only": True,
        "provider_admission_paused": provider_paused,
        "read_get_attempt_upper_bound": 4 * len(candidates),
        "get_attempts": total_gets_attempted,
        "live_observations": sum(row["status"] == "LIVE_PARTIAL_READ"
                                 for row in observations),
        "unknown_observations": sum(row["status"] != "LIVE_PARTIAL_READ"
                                    for row in observations),
        "issue_ownership_verified": False, "required_ci_verified": False,
        "branch_protection_verified": False, "independent_review_verified": False,
        "merge_authorized": False, "claim_authorized": False,
        "action_executed": False,
        "no_candidate_does_not_prove_no_existing_pr": True,
    }


def owner_html(report):
    """Portable script-free owner view of one bounded live PR observation."""
    esc = lambda v: html.escape(str(v), quote=True)
    rows = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; "
        "style-src 'unsafe-inline'; base-uri 'none'; form-action 'none';\">",
        "<title>zSentinel | Current PR candidate observation</title>",
        "<style>body{max-width:960px;margin:auto;padding:2rem;"
        "font:16px/1.5 system-ui,sans-serif;background:#f6f8fc;color:#132335}"
        "section{background:white;padding:1.3rem;margin:1.3rem 0;"
        "border:1px solid #b9cadc;border-left:5px solid #ad6413;"
        "border-radius:.5rem;overflow-wrap:anywhere}"
        "section.observed{border-left-color:#1d665c}h1{font-size:1.7rem}"
        "h2{font-size:1.15rem}a{color:#07569e}a:focus-visible{outline:3px solid #07569e}"
        "code{overflow-wrap:anywhere}small{color:#42586b}dl{display:grid;"
        "grid-template-columns:max-content 1fr;gap:.3rem 1.3rem}"
        "dt{font-weight:700}dd{margin:0}summary{cursor:pointer}</style></head><body>",
        "<header><h1>zSentinel — live PR candidate desk</h1>",
        "<p><strong>Read-only owner handoff.</strong> Live partial GitHub "
        "observations never establish required CI, reviewer independence, "
        "active owner, branch protection or merge entitlement.</p>",
        "<p>Source: <a href='" + esc(report["original_issue_url"]) +
        "' rel='noopener noreferrer'>" + esc(report["original_issue_url"]) +
        "</a> · checked: <code>" + esc(report["observed_at_utc"]) + "</code></p>",
        "<p>Observed " + str(report["live_observations"]) + " / " +
        str(report["candidate_count"]) + " referenced candidates; " +
        str(report["unknown_observations"]) + " UNKNOWN · GET attempts " +
        str(report["get_attempts"]) +
        (" · provider denied, remaining requests stopped"
         if report["provider_admission_paused"] else "") + "</p></header><main>",
    ]
    for row in report["observations"]:
        observed = row["status"] == "LIVE_PARTIAL_READ"
        rows.extend([
            "<section" + (" class='observed'" if observed else "") + ">",
            "<h2><a href='" + esc(row["url"]) +
            "' target='_blank' rel='noopener noreferrer'>PR #" +
            str(row["number"]) + "</a> — " + esc(row["status"]) + "</h2>",
            "<dl><dt>Current HEAD</dt><dd><code>" +
            esc(row.get("exact_head_sha", "UNKNOWN")) + "</code></dd>",
            "<dt>PR state</dt><dd>" + esc(row.get("observed_state", "UNKNOWN")) +
            " · draft: " + esc(row.get("observed_draft", "UNKNOWN")) +
            " · reported merged: " + esc(row.get("reported_merged", "UNKNOWN")) +
            "</dd>",
            "<dt>Historical link</dt><dd>" + esc(row["source_relationship"]) +
            " · draft at source: " + esc(row["source_draft"]) + "</dd>",
            "<dt>Required CI</dt><dd>UNKNOWN · sampled check runs are not "
            "branch-protection rules</dd>",
            "<dt>Independent review / active owner / deployment</dt>"
            "<dd>UNKNOWN / UNKNOWN / NOT AUTHORIZED</dd>",
            "<dt>Next human action</dt><dd>" + esc(row["next_human_step"]) +
            "</dd></dl>",
        ])
        checks = row.get("check_runs")
        if checks:
            rows.extend([
                "<details><summary>Sampled exact-head check states</summary>",
                "<p>Sampled " + str(checks["sampled_count"]) + " of " +
                str(checks["provider_total_count"]) +
                (" (PARTIAL)" if checks["sample_incomplete"] else "") +
                " check runs; all required-check identities UNKNOWN.</p><ul>",
            ])
            for key, count in checks["sample_status_counts"].items():
                rows.append("<li>" + esc(key) + ": " + str(count) + "</li>")
            rows.append("</ul></details>")
        reviews = row.get("reviews")
        if reviews:
            rows.extend([
                "<details><summary>Sampled review states</summary><p>" +
                str(reviews["sampled_count"]) +
                (" records (PARTIAL sample possible)." if reviews["may_be_truncated"]
                 else " sampled records.") +
                " Approval independence and current state UNKNOWN.</p><ul>",
            ])
            for key, count in reviews["observed_review_states"].items():
                rows.append("<li>" + esc(key) + ": " + str(count) + "</li>")
            rows.append("</ul></details>")
        if row.get("secondary_source_gaps"):
            rows.extend([
                "<details><summary>Unavailable secondary GitHub sources</summary>",
                "<ul>",
            ])
            rows.extend("<li>" + esc(reason) + "</li>"
                        for reason in row["secondary_source_gaps"])
            rows.append("</ul></details>")
        if "reason" in row:
            rows.append("<p><strong>Provider source:</strong> " +
                        esc(row["reason"]) + "</p>")
        rows.append("</section>")
    rows.extend([
        "</main><footer><small>No write endpoints, workflow dispatch, "
        "claim, merge, deployment or runner operation invoked. "
        "Every current release gate remains independently authoritative."
        "</small></footer></body></html>\\n",
    ])
    return "".join(rows)


def markdown(report):
    lines = [
        "# zSentinel · live candidate PR snapshot (read-only)",
        "",
        "**Observed PR information only; NO CI, review, ownership or merge permission.**",
        "",
        "- Issue: " + report["original_issue_url"],
        "- Observed: " + report["observed_at_utc"],
        "- Source candidates: " + str(report["source_candidate_count"]),
        "- Live partial reads: " + str(report["live_observations"]),
        "- UNKNOWN candidates: " + str(report["unknown_observations"]),
        "- GitHub requests attempted: " + str(report["get_attempts"]),
        "- Provider admission paused: " + str(report["provider_admission_paused"]),
        "",
    ]
    for row in report["observations"]:
        lines.extend([
            "## [PR #" + str(row["number"]) + "](" + row["url"] + ")",
            "",
            "- Live read: " + row["status"],
            "- Observed HEAD: " + row.get("exact_head_sha", "UNKNOWN"),
            "- Observed state: " + row.get("observed_state", "UNKNOWN"),
            "- Historical source link: " + row["source_relationship"],
            "- Historical draft: " + str(row["source_draft"]),
            "- Live draft: " + str(row.get("observed_draft", "UNKNOWN")),
            "- Required CI: UNKNOWN (sampled check runs are not branch rules)",
            "- Independent review and active owner: UNKNOWN",
            "- Next human action: " + row["next_human_step"],
            "- Authority: claim=false, merge=false, deploy=false",
            "",
        ])
        if "check_runs" in row:
            checks = row["check_runs"]
            lines.extend([
                "- Check runs on observed SHA (sampled/total): " +
                str(checks["sampled_count"]) + "/" + str(checks["provider_total_count"]),
                "- Check states (sample only): " +
                json.dumps(checks["sample_status_counts"], sort_keys=True),
                "",
            ])
        for reason in row.get("secondary_source_gaps", []):
            lines.append("- Secondary source UNKNOWN: " + reason)
        if "reason" in row:
            lines.extend(["- Provider note: " + row["reason"], ""])
    lines.append("A live read can identify the next person to ask; it never approves a protected-main merge.")
    return "\n".join(lines) + "\n"

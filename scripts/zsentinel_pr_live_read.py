#!/usr/bin/env python3
"""Bounded live GitHub PR observations for the source-linked zSentinel owner desk.

This read-only adapter observes at most three historically referenced
candidate PRs for *one* exact issue. It deliberately cannot authorize a
merge, claim, rerun or deployment. GitHub API failures are UNKNOWN, never
a missing PR, available owner or green required CI.
"""
from __future__ import annotations

import hashlib
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
ISSUE_URL = re.compile(
    "^https://github[.]com/(Zennay)/([A-Za-z0-9_.-]{1,100})/issues/([1-9][0-9]*)$"
)
MAX_BODY_BYTES = 160 * 1024
MAX_DISCOVERY_BODY_BYTES = 2 * 1024 * 1024
MAX_CANDIDATES = 3
MAX_LIST_PAGE = 30
MAX_DISCOVERY_PAGES = 4  # Bounded open-PR inventory; never an exhaustive ownership claim.


class GitHubReadError(ValueError):
    """GitHub public/private source was not usable for a non-authorizing view."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise GitHubReadError("GitHub API redirect refused")


def utc():
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _limited_json(path, *, token=None, max_body_bytes=MAX_BODY_BYTES):
    """One fixed-host HTTPS GitHub GET, no redirects, strict byte cap."""
    if (max_body_bytes not in (MAX_BODY_BYTES, MAX_DISCOVERY_BODY_BYTES)
            or (max_body_bytes == MAX_DISCOVERY_BODY_BYTES
                and "/pulls?state=open&per_page=" not in path)):
        raise GitHubReadError("oversized read requires the sole bounded PR page")
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
            data = response.read(max_body_bytes + 1)
            if len(data) > max_body_bytes:
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


def _issue_summary(payload, full_name, number):
    """Current issue state, never proof a fix landed or was deployed."""
    if (type(payload) is not dict or type(payload.get("number")) is not int
            or payload["number"] != number
            or payload.get("pull_request") is not None
            or payload.get("state") not in ("open", "closed")
            or payload.get("repository_url") !=
                "https://api.github.com/repos/" + full_name):
        raise GitHubReadError("live issue identity or status is unavailable")
    state = payload["state"]
    reason = payload.get("state_reason")
    if reason not in (None, "completed", "not_planned", "reopened"):
        reason = "UNKNOWN"
    return {
        "observed_state": state,
        "reported_state_reason": reason or "UNKNOWN",
        "source_issue_still_open": state == "open",
        "closed_does_not_prove_fix_merged": True,
        "source_link_to_candidate_reverified": False,
        "owner_claim_verified": False,
        "issue_work_cleared": False,
        "observed_at_utc": utc(),
    }


def _body_issue_reference(payload, full_name, issue_number):
    """Inspect only the already-fetched PR body; never expose its contents.

    Textual GitHub references are hints, not current issue cross-reference
    graph proof, owner admission, closing automation or merged-main proof.
    """
    body = payload.get("body")
    if body is None:
        return "BODY_UNKNOWN", None
    if type(body) is not str:
        raise GitHubReadError("PR body is not canonical text")
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    if len(body) > 32768:
        return "BODY_TOO_LARGE_UNKNOWN", digest
    number = str(issue_number)
    local = re.search(r"(?<![A-Za-z0-9_])#" + number + r"(?![0-9])", body)
    full = re.search(
        r"https://github[.]com/" + re.escape(full_name) +
        r"/issues/" + number + r"(?![0-9])",
        body, flags=re.IGNORECASE,
    )
    if not local and not full:
        return "NO_TEXT_REFERENCE_IN_PR_BODY", digest
    close = re.search(
        r"\b(?:close|closes|closed|fix|fixes|fixed|resolve|resolves|resolved)"
        r"\s+(?:" + re.escape(full_name) + r")?#" + number + r"(?![0-9])",
        body, flags=re.IGNORECASE,
    )
    return (
        "CLOSING_KEYWORD_TEXT_OBSERVED" if close
        else "ISSUE_TEXT_MENTION_OBSERVED"
    ), digest


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
        "exact_head_checks_url": "https://github.com/" + full_name + "/commit/" + sha + "/checks",
        "pr_files_url": "https://github.com/" + full_name + "/pull/" + str(number) + "/files",
        "mergeable_hint": payload.get("mergeable") if type(payload.get("mergeable")) is bool else None,
        "updated_at_source": payload.get("updated_at") if isinstance(payload.get("updated_at"), str) else None,
    }


def _checks_summary(payload, sha, full_name):
    """A bounded incomplete HEAD-scoped observation, never a required-CI verdict."""
    if not isinstance(payload, dict) or type(payload.get("total_count")) is not int:
        raise GitHubReadError("check-run response missing total count")
    rows = payload.get("check_runs")
    count = payload["total_count"]
    if not isinstance(rows, list) or len(rows) > MAX_LIST_PAGE or count < len(rows):
        raise GitHubReadError("check-run sample invalid")
    sums = {"completed_success": 0, "completed_other": 0, "not_completed": 0}
    observed_names = []
    sampled_blocking_checks = []
    sampled_pending_checks = []
    # These are only hints from a first-page sample. They never establish
    # which checks branch protection requires or make CI green.
    for row in rows:
        if not isinstance(row, dict):
            raise GitHubReadError("invalid check-run row")
        source_sha = row.get("head_sha")
        if source_sha is not None and source_sha != sha:
            raise GitHubReadError("check-run does not belong to observed exact HEAD")
        status = row.get("status")
        conclusion = row.get("conclusion")
        check_id = row.get("id")
        check_link = (
            "https://github.com/" + full_name + "/runs/" + str(check_id)
            if type(check_id) is int and 1 <= check_id <= 2**53 - 1 else None
        )
        name = row.get("name")
        # Do not place provider-supplied control characters in human
        # handoffs. The check URL itself is built from canonical identity.
        clean_name = (
            "".join(c if c.isprintable() else " " for c in name)[:90]
            if isinstance(name, str) else "unnamed sampled check"
        )
        if (status == "completed" and conclusion in
                ("failure", "timed_out", "action_required")
                and len(sampled_blocking_checks) < 6):
            sampled_blocking_checks.append({
                "name": clean_name, "conclusion": conclusion, "url": check_link,
                "required_by_branch_protection": "UNKNOWN",
            })
        if status != "completed" and len(sampled_pending_checks) < 6:
            sampled_pending_checks.append({
                "name": clean_name, "status": str(status)[:32],
                "url": check_link, "required_by_branch_protection": "UNKNOWN",
            })
        if status == "completed":
            sums["completed_success" if row.get("conclusion") == "success"
                 else "completed_other"] += 1
        else:
            sums["not_completed"] += 1
        if clean_name not in observed_names:
            observed_names.append(clean_name)
    return {
        "sampled_count": len(rows), "provider_total_count": count,
        "sample_incomplete": count > len(rows),
        "sample_status_counts": sums, "sample_names": observed_names[:12],
        "sampled_blocking_checks": sampled_blocking_checks,
        "sampled_pending_checks": sampled_pending_checks,
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


def _discover_sampled_open_prs(issue_url, *, token=None):
    """Best-effort same-repo, issue-text hints from one open-PR page.

    A missing first-page mention does NOT prove there is no existing PR.
    GitHub's issue cross-reference graph is not reconstructed here.
    """
    match = ISSUE_URL.fullmatch(issue_url)
    if match is None:
        raise GitHubReadError("discovery issue identity is not canonical")
    owner, repo, number = match.groups()
    full_name = owner + "/" + repo
    path = "/repos/" + quote(owner, safe="") + "/" + quote(repo, safe="")
    candidates, seen, matched_total, skipped_rows = [], set(), 0, 0
    scanned_rows = 0
    pages_scanned = 0
    first_page_rows = None
    last_page_short = False
    # Pagination is limited to four 30-PR pages. The last short page is a
    # historical observation, not durable proof that no other repair exists.
    for page_number in range(1, MAX_DISCOVERY_PAGES + 1):
        payload = _limited_json(
            path + "/pulls?state=open&per_page=" + str(MAX_LIST_PAGE) +
            "&page=" + str(page_number),
            token=token, max_body_bytes=MAX_DISCOVERY_BODY_BYTES,
        )
        if not isinstance(payload, list) or len(payload) > MAX_LIST_PAGE:
            raise GitHubReadError("bounded GitHub open-PR page has invalid shape")
        pages_scanned += 1
        scanned_rows += len(payload)
        if first_page_rows is None:
            first_page_rows = len(payload)
        last_page_short = len(payload) < MAX_LIST_PAGE
        for row in payload:
            if not isinstance(row, dict):
                skipped_rows += 1
                continue
            identity = row.get("number")
            base = row.get("base")
            if (type(identity) is not int or not 1 <= identity <= 100000000
                    or not isinstance(base, dict)
                    or not isinstance(base.get("repo"), dict)
                    or base["repo"].get("full_name") != full_name
                    or row.get("state") != "open"
                    or type(row.get("draft")) is not bool):
                skipped_rows += 1
                continue
            if identity in seen:
                skipped_rows += 1
                continue
            seen.add(identity)
            status, _ = _body_issue_reference(row, full_name, int(number))
            if status not in (
                "ISSUE_TEXT_MENTION_OBSERVED", "CLOSING_KEYWORD_TEXT_OBSERVED"
            ):
                continue
            matched_total += 1
            if len(candidates) < MAX_CANDIDATES:
                candidates.append({
                    "number": identity,
                    "url": "https://github.com/" + full_name + "/pull/" + str(identity),
                    "draft_at_source": row["draft"],
                    "relationship_at_source": (
                        "closing_keyword_only" if status == "CLOSING_KEYWORD_TEXT_OBSERVED"
                        else "text_reference_only"
                    ),
                    "candidate_origin": "sampled_open_pr_first_page_text_only",
                })
        if last_page_short:
            break
    return candidates, {
        "performed": True,
        "first_page_rows": first_page_rows,
        "first_page_limit": MAX_LIST_PAGE,
        "pages_scanned": pages_scanned,
        "max_pages": MAX_DISCOVERY_PAGES,
        "open_pr_rows_scanned": scanned_rows,
        "last_page_short_at_observation": last_page_short,
        "pagination_window_full": not last_page_short,
        "candidate_text_matches_in_page": matched_total,
        "candidate_records_returned": len(candidates),
        "skipped_or_malformed_first_page_rows": skipped_rows,
        "skipped_or_malformed_rows_in_window": skipped_rows,
        "candidate_list_truncated": matched_total > len(candidates),
        "source_page_may_be_incomplete": True,
        "existing_repair_absence_verified": False,
        "issue_pr_relationship_verified": False,
    }


def observe_candidate_prs(issue, *, token=None, source_case=None,
                          discover_unlinked=False):
    """Read exactly an admitted issue's referenced PRs; leave each result untrusted."""
    candidates = issue.get("candidate_prs", [])
    if not isinstance(candidates, list) or len(candidates) > MAX_CANDIDATES:
        raise GitHubReadError("unbounded or absent candidate source")
    if discover_unlinked and candidates:
        raise GitHubReadError(
            "original issue already has sampled PRs; use normal live PR review"
        )
    if not candidates and not discover_unlinked:
        raise GitHubReadError(
            "issue has no sampled PR; use explicit first-page discovery or refresh source"
        )
    if (type(source_case) is not dict
            or source_case.get("policy") != "zsentinel-recovery-case-handoff-v1"):
        raise GitHubReadError(
            "original admitted recovery-case provenance required for live PR review"
        )
    provenance = {
        "case_export_utc": source_case["source_generated_at_utc"],
        "issue_source_observed_utc": source_case["source_issues_observed_at_utc"],
        "case_stale_at_import": source_case["source_case_stale"],
        "issue_source_stale_at_import": source_case["source_issues_stale"],
        "issue_sample_truncated": source_case["source_issues_truncated"],
        "historical_candidate_set_is_complete": False,
        "current_issue_status_verified": False,
        "current_issue_pr_link_verified": False,
    }
    source_url = issue["source_urls"][0]
    historical_candidates = len(candidates)
    discovery = {
        "performed": False,
        "source_page_may_be_incomplete": True,
        "existing_repair_absence_verified": False,
        "issue_pr_relationship_verified": False,
    }
    extra_get_attempts = 0
    if discover_unlinked:
        # Optional operator-directed discovery only: one first-page GET,
        # then the same bounded, double-read candidate observation as normal.
        # A matching body phrase is never considered an authorized issue link.
        candidates, discovery = _discover_sampled_open_prs(
            source_url, token=token,
        )
        extra_get_attempts = discovery["pages_scanned"]
    issue_match = ISSUE_URL.fullmatch(source_url)
    if issue_match is None:
        raise GitHubReadError("source issue is not a canonical repository issue")
    expected_repo = source_url.rsplit("/issues/", 1)[0]
    issue_repo_name = issue_match[1] + "/" + issue_match[2]
    issue_api = (
        "/repos/" + quote(issue_match[1], safe="") + "/" +
        quote(issue_match[2], safe="") + "/issues/" + issue_match[3]
    )
    current_issue = {
        "status": "UNKNOWN_PROVIDER",
        "observed_state": "UNKNOWN",
        "source_issue_still_open": "UNKNOWN",
        "source_link_to_candidate_reverified": False,
        "owner_claim_verified": False,
        "issue_work_cleared": False,
    }
    observations = []
    provider_paused = False
    total_gets_attempted = extra_get_attempts
    try:
        total_gets_attempted += 1
        current_issue = _issue_summary(
            _limited_json(issue_api, token=token),
            issue_repo_name, int(issue_match[3]),
        )
        current_issue["status"] = "LIVE_ISSUE_READ"
    except GitHubReadError as exc:
        current_issue["reason"] = str(exc)
        if "authentication, quota or provider admission" in str(exc):
            provider_paused = True
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
            "candidate_origin": candidate.get(
                "candidate_origin", "historical_case_issue_reference"
            ),
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
            before_payload = _limited_json(
                api + "/pulls/" + str(number), token=token,
            )
            before = _pr_summary(before_payload, full_name, number)
            before_reference, before_body_digest = _body_issue_reference(
                before_payload, full_name, int(issue_match[3]),
            )
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
                ), before["exact_head_sha"], full_name)
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
            after_payload = _limited_json(
                api + "/pulls/" + str(number), token=token
            )
            after = _pr_summary(after_payload, full_name, number)
            after_reference, after_body_digest = _body_issue_reference(
                after_payload, full_name, int(issue_match[3]),
            )
            if (before_body_digest != after_body_digest
                    or before_reference != after_reference):
                raise GitHubReadError(
                    "PR description changed during live read; text relation UNKNOWN"
                )
            if (before["exact_head_sha"] != after["exact_head_sha"] or
                    before["observed_state"] != after["observed_state"] or
                    before["reported_merged"] != after["reported_merged"] or
                    before["observed_draft"] != after["observed_draft"] or
                    before["target_branch"] != after["target_branch"]):
                raise GitHubReadError("PR changed during live read; no stable exact HEAD")
            base.update(before)
            base["observed_at_utc"] = utc()
            base["status"] = "LIVE_PARTIAL_READ"
            base["pr_body_issue_text_observation"] = before_reference
            base["pr_body_text_does_not_verify_issue_link"] = True
            base["secondary_source_gaps"] = source_gaps
            if checks is not None:
                base["check_runs"] = checks
            if reviews is not None:
                base["reviews"] = reviews
            if before["reported_merged"]:
                owner_action = "verify_reported_merge_on_main_and_postmerge_health"
                review_rank = 5
            elif before["observed_state"] != "open":
                owner_action = "locate_replacement_pr_for_closed_unmerged_source"
                review_rank = 6
            elif before["target_branch"] != "main":
                owner_action = "check_target_branch_and_actual_release_intent"
                review_rank = 4
            elif before["observed_draft"]:
                owner_action = "finish_existing_draft_with_authorized_owner"
                review_rank = 3
            elif before_reference == "NO_TEXT_REFERENCE_IN_PR_BODY":
                owner_action = "recheck_historical_issue_to_pr_link_before_merge"
                review_rank = 2
            elif checks is not None and checks["sampled_blocking_checks"]:
                owner_action = "inspect_sampled_failed_head_checks_before_merge"
                review_rank = 0
            elif checks is not None and checks["sampled_pending_checks"]:
                owner_action = "inspect_sampled_pending_head_jobs_and_runner_admission"
                review_rank = 1
            elif checks is None:
                owner_action = "recover_missing_check_source_before_merge_review"
                review_rank = 2
            else:
                owner_action = "verify_required_ci_independent_review_protection_and_owner"
                review_rank = 2
            if (current_issue["status"] == "LIVE_ISSUE_READ"
                    and current_issue["observed_state"] == "closed"
                    and not before["reported_merged"]):
                # The source issue has been closed since the historical
                # import. Do not turn its old PR into an unsolicited fix.
                owner_action = "verify_issue_closure_and_existing_repair_before_reopening"
                review_rank = 5
            base["next_human_step"] = owner_action
            base["owner_review_priority"] = review_rank
            base["release_gate_summary"] = {
                "pr_head_reobserved": True,
                "target_is_main": before["target_branch"] == "main",
                "sampled_check_source_available": checks is not None,
                "sampled_review_source_available": reviews is not None,
                "required_ci_status": "UNKNOWN",
                "independent_review_status": "UNKNOWN",
                "active_owner_status": "UNKNOWN",
                "safe_to_merge": False,
            }
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
        "current_issue": current_issue,
        "historical_case_provenance": provenance,
        "observed_at_utc": utc(),
        "candidate_count": len(candidates),
        "source_candidate_count": historical_candidates,
        "optional_unlinked_discovery": discovery,
        "observations": observations, "sample_scope_only": True,
        "suggested_owner_review_order": [
            {"number": row["number"], "url": row["url"],
             "next_human_step": row["next_human_step"],
             "owner_review_priority": row.get("owner_review_priority", 7)}
            for row in sorted(
                observations, key=lambda item: (
                    item.get("owner_review_priority", 7), item["number"]
                )
            )
        ],
        "provider_admission_paused": provider_paused,
        "read_get_attempt_upper_bound": (
            1 + (MAX_DISCOVERY_PAGES if discover_unlinked else 0) +
            4 * len(candidates)
        ),
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
        "<p><strong>Current issue GET:</strong> " +
        esc(report["current_issue"]["status"]) +
        " · state: " + esc(report["current_issue"]["observed_state"]) +
        " · reason: " + esc(report["current_issue"].get(
            "reported_state_reason", "UNKNOWN")) +
        " · CLOSED never means a fix is merged or the worker claim released."
        " This single issue read is not a continuous issue-state guarantee.</p>",
        "<p><strong>Historical issue source (not reobserved):</strong> " +
        esc(report["historical_case_provenance"]["issue_source_observed_utc"] or "UNKNOWN") +
        " · original case export: " +
        esc(report["historical_case_provenance"]["case_export_utc"]) +
        (" · ISSUE SOURCE STALE" if report["historical_case_provenance"][
            "issue_source_stale_at_import"] else "") +
        (" · PARTIAL issue sample" if report["historical_case_provenance"][
            "issue_sample_truncated"] else "") +
        ". Current PR observations do NOT refresh historical issue links.</p>",
        "<p>Observed " + str(report["live_observations"]) + " / " +
        str(report["candidate_count"]) + " referenced candidates; " +
        str(report["unknown_observations"]) + " UNKNOWN · GET attempts " +
        str(report["get_attempts"]) +
        (" · provider denied, remaining requests stopped"
         if report["provider_admission_paused"] else "") +
        "</p></header><main>",
    ]
    discovery = report["optional_unlinked_discovery"]
    if discovery["performed"]:
        rows.append(
            "<section><h2>Opt-in first-page open-PR discovery</h2><p>"
            "This is an incomplete text-reference search, NOT evidence "
            "that no other repair PR exists.</p><p>Open PRs sampled: " +
            str(discovery["first_page_rows"]) + " / first " +
            str(discovery["first_page_limit"]) +
            " · text mentions found: " +
            str(discovery["candidate_text_matches_in_page"]) +
            " · candidates retained: " +
            str(discovery["candidate_records_returned"]) +
            " · skipped invalid records: " +
            str(discovery["skipped_or_malformed_first_page_rows"]) +
            (" · candidate sample truncated" if discovery[
                "candidate_list_truncated"] else "") + ".</p></section>"
        )
    rows.extend([
        "<nav aria-label='Owner next steps'>",
        "<h2>Existing PRs to inspect first (advisory order)</h2>",
        "<p>Sampled failing check and waiting-job signals are triage hints, "
        "not mandatory-branch check proof. Always verify current claims and "
        "protected-main gates before acting.</p><ol>",
    ])
    if not report["observations"]:
        rows.append(
            "<li>No matching PR in this sampled page. This is NOT proof "
            "that the issue has no existing repair PR: expand source "
            "discovery with an authorized owner before creating work.</li>"
        )
    for candidate in report["suggested_owner_review_order"]:
        rows.append(
            "<li><a href='" + esc(candidate["url"]) +
            "' target='_blank' rel='noopener noreferrer'>PR #" +
            str(candidate["number"]) + "</a>: " +
            esc(candidate["next_human_step"]) + "</li>"
        )
    rows.append("</ol></nav>")
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
            " · target: " + esc(row.get("target_branch", "UNKNOWN")) +
            "</dd>",
            "<dt>Reference origin</dt><dd>" +
            esc(row["candidate_origin"]) + " · " +
            esc(row["source_relationship"]) +
            " · draft in corresponding sample: " + esc(row["source_draft"]) +
            "</dd>",
            "<dt>Current PR description text</dt><dd>" +
            esc(row.get("pr_body_issue_text_observation", "UNKNOWN")) +
            " · textual signal only, not a verified GitHub issue relationship"
            "</dd>",
            "<dt>Required CI</dt><dd>UNKNOWN · sampled check runs are not "
            "branch-protection rules</dd>",
            "<dt>Independent review / active owner / deployment</dt>"
            "<dd>UNKNOWN / UNKNOWN / NOT AUTHORIZED</dd>",
            "<dt>Next human action</dt><dd>" + esc(row["next_human_step"]) +
            "</dd></dl>",
        ])
        if observed:
            rows.append(
                "<p><a href='" + esc(row["exact_head_checks_url"]) +
                "' target='_blank' rel='noopener noreferrer'>Inspect checks "
                "for this observed exact HEAD</a> · <a href='" +
                esc(row["pr_files_url"]) +
                "' target='_blank' rel='noopener noreferrer'>Review PR changes"
                "</a></p>"
            )
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
            rows.append("</ul>")
            if checks["sampled_blocking_checks"]:
                rows.append("<p><strong>Observed failing HEAD checks — "
                            "not necessarily branch-required:</strong></p><ul>")
                for check in checks["sampled_blocking_checks"]:
                    label = check["name"] + " (" + check["conclusion"] + ")"
                    if check["url"] is not None:
                        rows.append(
                            "<li><a href='" + esc(check["url"]) +
                            "' target='_blank' rel='noopener noreferrer'>" +
                            esc(label) + "</a></li>"
                        )
                    else:
                        rows.append("<li>" + esc(label) + "</li>")
                rows.append("</ul>")
            if checks["sampled_pending_checks"]:
                rows.append("<p><strong>Observed waiting HEAD jobs — "
                            "not necessarily branch-required:</strong></p><ul>")
                for check in checks["sampled_pending_checks"]:
                    label = check["name"] + " (" + check["status"] + ")"
                    if check["url"] is not None:
                        rows.append(
                            "<li><a href='" + esc(check["url"]) +
                            "' target='_blank' rel='noopener noreferrer'>" +
                            esc(label) + "</a></li>"
                        )
                    else:
                        rows.append("<li>" + esc(label) + "</li>")
                rows.append("</ul>")
            rows.append("</details>")
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
        "- Current issue read: " + report["current_issue"]["status"],
        "- Current issue state: " + report["current_issue"]["observed_state"],
        "- Current issue source was reobserved once, not continuously",
        "- Closed issue implies merged repair: NO",
        "- Observed live PRs: " + report["observed_at_utc"],
        "- Original case export: " + report["historical_case_provenance"]["case_export_utc"],
        "- Historical issue observed: " +
        (report["historical_case_provenance"]["issue_source_observed_utc"] or "UNKNOWN"),
        "- Historical issue source stale at import: " +
        str(report["historical_case_provenance"]["issue_source_stale_at_import"]),
        "- Historical issue sample truncated: " +
        str(report["historical_case_provenance"]["issue_sample_truncated"]),
        "- Current issue PR relationship reverified: NO",
        "- Historical issue-PR candidates: " +
        str(report["source_candidate_count"]),
        "- Live partial reads: " + str(report["live_observations"]),
        "- UNKNOWN candidates: " + str(report["unknown_observations"]),
        "- GitHub requests attempted: " + str(report["get_attempts"]),
        "- Provider admission paused: " + str(report["provider_admission_paused"]),
        "",
    ]
    discovery = report["optional_unlinked_discovery"]
    if discovery["performed"]:
        lines.extend([
            "## Opt-in bounded existing-PR discovery",
            "",
            "- Sampled only first " + str(discovery["first_page_limit"]) +
            " open-PR records, observed " + str(discovery["first_page_rows"]),
            "- Candidate text references found: " +
            str(discovery["candidate_text_matches_in_page"]),
            "- Candidates retained for live inspection: " +
            str(discovery["candidate_records_returned"]),
            "- Skipped malformed rows: " +
            str(discovery["skipped_or_malformed_first_page_rows"]),
            "- Historical relation reverified: NO",
            "- No matching PR in this page proves absence: NO",
            "",
        ])
    lines.extend([
        "## Suggested next existing PR reviews (non-authorizing)",
        "",
    ])
    if not report["observations"]:
        lines.append(
            "- No referenced PR observed in this bounded sample. "
            "An existing PR may still exist outside the sampled page."
        )
    for candidate in report["suggested_owner_review_order"]:
        lines.append(
            "- [PR #" + str(candidate["number"]) + "](" +
            candidate["url"] + ") — " + candidate["next_human_step"]
        )
    lines.append("")
    for row in report["observations"]:
        lines.extend([
            "## [PR #" + str(row["number"]) + "](" + row["url"] + ")",
            "",
            "- Live read: " + row["status"],
            "- Observed HEAD: " + row.get("exact_head_sha", "UNKNOWN"),
            "- Exact-HEAD checks: " + row.get("exact_head_checks_url", "UNKNOWN"),
            "- PR changed files: " + row.get("pr_files_url", "UNKNOWN"),
            "- Observed state: " + row.get("observed_state", "UNKNOWN"),
            "- Observed target branch: " + row.get("target_branch", "UNKNOWN"),
            "- Candidate origin: " + row["candidate_origin"],
            "- Source-observed relationship: " + row["source_relationship"],
            "- Current PR body issue text: " +
            row.get("pr_body_issue_text_observation", "UNKNOWN"),
            "- Text mention proves current PR-issue link: NO",
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
            for check in checks["sampled_blocking_checks"]:
                lines.append(
                    "- Sampled failing check: " + check["conclusion"] +
                    " · " + (check["url"] or "URL UNKNOWN")
                )
            for check in checks["sampled_pending_checks"]:
                lines.append(
                    "- Sampled waiting check: " + check["status"] +
                    " · " + (check["url"] or "URL UNKNOWN")
                )
            lines.append("")
        for reason in row.get("secondary_source_gaps", []):
            lines.append("- Secondary source UNKNOWN: " + reason)
        if "reason" in row:
            lines.extend(["- Provider note: " + row["reason"], ""])
    lines.append("A live read can identify the next person to ask; it never approves a protected-main merge.")
    return "\n".join(lines) + "\n"

#!/usr/bin/env python3
"""zSentinel read-only GitHub issue inbox: bounded live P0/P1-first operator view.

The inbox observes public/private issues only where the caller has ordinary
GitHub GET access. It does not claim that missing priority labels mean a
healthy project, that a title hint is an official priority, or that repairs
are safe to merge. Neither titles nor issue bodies leave the in-memory read.
"""
from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from urllib.parse import urlencode

from zsentinel_pr_live_read import (
    GitHubReadError, MAX_DISCOVERY_BODY_BYTES, _limited_json,
)

REPO = re.compile(r"Zennay/([A-Za-z0-9_.-]{1,100})\Z")
ISSUE = re.compile(
    r"https://github[.]com/Zennay/([A-Za-z0-9_.-]{1,100})/issues/([1-9][0-9]*)\Z"
)
MAX_PAGES = 3
PAGE_SIZE = 30
MAX_DISPLAY = 45
MAX_LABELS = 100
RECOGNIZED = {
    prefix + priority for prefix in ("", "priority:", "priority/")
    for priority in ("p0", "p1", "p2", "p3")
}


def _safe_issue(item, repository, *, observed_at):
    """Reduce untrusted GitHub issue into non-sensitive advisory metadata."""
    if not isinstance(item, dict) or item.get("pull_request") is not None:
        return None
    number = item.get("number")
    url = item.get("html_url")
    if type(number) is not int or number < 1 or not isinstance(url, str):
        return None
    match = ISSUE.fullmatch(url)
    if match is None or match[1] != repository or int(match[2]) != number:
        return None
    if item.get("state") != "open":
        return None
    if item.get("repository_url") != (
        "https://api.github.com/repos/Zennay/" + repository
    ):
        return None
    updated = item.get("updated_at")
    updated_at = None
    if isinstance(updated, str) and len(updated) <= 32:
        try:
            parsed = datetime.fromisoformat(updated.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                updated_at = parsed.astimezone(timezone.utc).isoformat().replace(
                    "+00:00", "Z"
                )
        except ValueError:
            pass

    labels = item.get("labels")
    labels_complete = isinstance(labels, list) and len(labels) <= MAX_LABELS
    matched = set()
    if labels_complete:
        for label in labels:
            name = label.get("name") if isinstance(label, dict) else None
            if not isinstance(name, str) or len(name) > 100:
                labels_complete = False
                break
            normalized = name.strip().lower().replace(" ", "")
            if normalized in RECOGNIZED:
                matched.add(normalized[-2:].upper())
    conflict = len(matched) > 1
    verified_label = (
        next(iter(matched)) if labels_complete and len(matched) == 1
        else "UNKNOWN"
    )
    age_hours = None
    if updated_at is not None:
        try:
            parsed_update = datetime.fromisoformat(
                updated_at.replace("Z", "+00:00")
            )
            age_seconds = (observed_at - parsed_update).total_seconds()
            if age_seconds >= 0:
                age_hours = int(age_seconds // 3600)
        except ValueError:
            pass
    no_issue_update_for_72h = (
        age_hours >= 72 if age_hours is not None else "UNKNOWN"
    )
    title = item.get("title")
    title_hint = (
        "P0" if isinstance(title, str) and
        re.match(r"^\s*P0\b(?:\s|[:\-])", title, re.IGNORECASE) else
        "P1" if isinstance(title, str) and
        re.match(r"^\s*P1\b(?:\s|[:\-])", title, re.IGNORECASE) else "NONE"
    )
    # Priority rank puts explicitly labelled P0/P1 first. Title-only
    # hints are lower-trust navigation signals; P2/P3 follow them.
    # Conflicting labels cannot be silently overruled by title text.
    if verified_label == "P0":
        rank = 0
    elif verified_label == "P1":
        rank = 1
    elif not conflict and title_hint == "P0":
        rank = 2
    elif not conflict and title_hint == "P1":
        rank = 3
    elif verified_label == "P2":
        rank = 4
    elif verified_label == "P3":
        rank = 5
    else:
        rank = 6

    return {
        "number": number, "url": url, "updated_at_utc": updated_at,
        "last_github_update_age_hours": age_hours,
        "no_github_issue_update_for_72h": no_issue_update_for_72h,
        "activity_age_does_not_prove_worker_stall": True,
        "priority_from_explicit_labels": verified_label,
        "explicit_priority_label_conflict": conflict,
        "label_source_complete": labels_complete,
        "unverified_title_prefix_hint": title_hint,
        "triage_rank": rank,
        "state": "open",
        "source_type": "github_recent_open_issue",
        "owner_next_step": "inspect_current_issue_and_existing_repair_prs",
        "followup_command": (
            "python3 scripts/zsentinel_recovery_policy.py"
            " --live-issue-url " + url + " --format html"
        ),
        "followup_command_executed": False,
        "current_issue_details_fetched": False,
        "repair_prs_checked": False,
        "issue_owner_claim_verified": False,
        "workflow_gate_verified": False,
        "claim_authorized": False,
        "merge_authorized": False,
        "deploy_authorized": False,
    }


def observe_repo_inbox(repo_full_name, *, token=None):
    """Recent issue inventory plus bounded P0/P1 recall when recent window ends.

    The issue-only search preserves original GitHub source ordering; the
    result is still a bounded snapshot, not an exhaustive owner assignment.
    P0/P1 title recall is a navigation hint and must never grant authority.
    """
    if not isinstance(repo_full_name, str):
        raise GitHubReadError("inbox repository must be owner/name")
    match = REPO.fullmatch(repo_full_name)
    if match is None:
        raise GitHubReadError("inbox repository must be Zennay/<repo>")
    name = match[1]
    query_prefix = "repo:Zennay/" + name + " is:issue is:open"
    observed_at = datetime.now(timezone.utc)
    collected = {}
    total_rows = 0
    prs_excluded = 0
    invalid_excluded = 0
    source_error = None
    attempts = 0
    pages_read = 0
    provider_total_count = None
    provider_incomplete = False
    recent_window_truncated = True
    recall_performed = False
    recall_sources = []

    def read_page(query, page):
        nonlocal attempts
        attempts += 1
        payload = _limited_json(
            "/search/issues?" + urlencode({"q": query}) +
            "&sort=updated&order=desc&per_page=" + str(PAGE_SIZE) +
            "&page=" + str(page),
            token=token, max_body_bytes=MAX_DISCOVERY_BODY_BYTES,
        )
        if not isinstance(payload, dict):
            raise GitHubReadError("GitHub issue-only search response malformed")
        items = payload.get("items")
        count = payload.get("total_count")
        incomplete = payload.get("incomplete_results")
        if (not isinstance(items, list) or len(items) > PAGE_SIZE
                or type(count) is not int or count < 0
                or type(incomplete) is not bool):
            raise GitHubReadError("GitHub issue-only search coverage unavailable")
        return items, count, incomplete

    def admit_rows(items, source):
        nonlocal total_rows, prs_excluded, invalid_excluded
        total_rows += len(items)
        for raw in items:
            if isinstance(raw, dict) and "pull_request" in raw:
                prs_excluded += 1
                continue
            item = _safe_issue(raw, name, observed_at=observed_at)
            if item is None:
                invalid_excluded += 1
                continue
            if source != "recent" and (
                    item["unverified_title_prefix_hint"] != source
                    and item["priority_from_explicit_labels"] != source):
                # GitHub text-search words need not start the issue title.
                # Drop unrelated search hits rather than inventing urgency.
                continue
            existing = collected.get(item["number"])
            if existing is None:
                item["inbox_source_windows"] = [source]
                collected[item["number"]] = item
            elif source not in existing["inbox_source_windows"]:
                existing["inbox_source_windows"].append(source)

    # Read recent issues first. Exact repo+issue-only filters exclude PR
    # storms from the inventory. Any GitHub permission denial ends this
    # entire run; no fallback retries to bypass quota or owner admission.
    for page in range(1, MAX_PAGES + 1):
        try:
            items, count, incomplete = read_page(query_prefix, page)
        except GitHubReadError as exc:
            source_error = str(exc)
            break
        pages_read += 1
        provider_total_count = count
        provider_incomplete = provider_incomplete or incomplete
        admit_rows(items, "recent")
        recent_window_truncated = (
            len(items) == PAGE_SIZE and page * PAGE_SIZE < count
        )
        if not recent_window_truncated:
            break

    # When all recent open issues fit, the P0/P1 search is redundant.
    # If recent coverage is truncated or incomplete, retrieve the first
    # page of each P0/P1 title recall separately (2 extra GETs at most).
    # Never promote a title hint into an exact label or owner claim.
    if source_error is None and (recent_window_truncated or provider_incomplete):
        recall_performed = True
        for priority in ("P0", "P1"):
            try:
                items, count, incomplete = read_page(
                    query_prefix + " " + priority + " in:title", 1
                )
            except GitHubReadError as exc:
                source_error = str(exc)
                break
            provider_incomplete = provider_incomplete or incomplete
            recall_sources.append({
                "priority": priority, "source_matches": count,
                "sampled_rows": len(items),
                "truncated": len(items) == PAGE_SIZE and count > PAGE_SIZE,
                "provider_incomplete": incomplete,
                "authority": False,
            })
            admit_rows(items, priority)

    issues = list(collected.values())
    issues.sort(key=lambda x: (
        x["triage_rank"],
        0 if x["no_github_issue_update_for_72h"] is True else 1,
        -(x["last_github_update_age_hours"] or 0),
    ))
    chosen = issues[:MAX_DISPLAY]
    by_rank = {}
    for item in issues:
        label = (
            item["priority_from_explicit_labels"]
            if item["priority_from_explicit_labels"] != "UNKNOWN"
            else "TITLE_HINT_" + item["unverified_title_prefix_hint"]
            if item["unverified_title_prefix_hint"] != "NONE"
            else "UNKNOWN"
        )
        by_rank[label] = by_rank.get(label, 0) + 1

    recall_partial = any(item["truncated"] or item["provider_incomplete"]
                         for item in recall_sources)
    return {
        "schema_version": 1, "policy": "zsentinel-live-github-issue-inbox-v1",
        "repository": repo_full_name,
        "observed_at_utc": observed_at.isoformat().replace("+00:00", "Z"),
        "observation_mode": "bounded_recent_github_open_issues_plus_priority_recall",
        "source": "github_rest_search_issues_issue_only",
        "pages_read": pages_read, "max_pages": MAX_PAGES,
        "items_per_page": PAGE_SIZE, "source_rows_scanned": total_rows,
        "pull_requests_excluded": prs_excluded,
        "provider_total_issue_count_at_observation": provider_total_count,
        "provider_incomplete_results": provider_incomplete,
        "recent_window_truncated": recent_window_truncated,
        "priority_recall_performed": recall_performed,
        "priority_recall_sources": recall_sources,
        "priority_recall_not_authoritative": True,
        "invalid_or_inaccessible_rows_excluded": invalid_excluded,
        "total_issues_seen": len(issues), "displayed_issues": len(chosen),
        "display_truncated": len(issues) > MAX_DISPLAY,
        "source_window_full": (
            recent_window_truncated and source_error is None
        ),
        "source_partial_or_unknown": (
            bool(source_error) or provider_incomplete
            or recent_window_truncated or recall_partial
        ),
        "source_error": source_error,
        "get_attempts": attempts,
        "read_get_attempt_upper_bound": MAX_PAGES + 2,
        "priority_breakdown_in_source_window": by_rank,
        "explicit_p0_p1_without_github_issue_update_72h": sum(
            row["priority_from_explicit_labels"] in ("P0", "P1")
            and row["no_github_issue_update_for_72h"] is True
            for row in issues
        ),
        "github_issue_update_age_does_not_prove_worker_stall": True,
        "unknown_priority_does_not_prove_low_severity": True,
        "no_issue_in_window_does_not_prove_absence": True,
        "title_prefix_priority_is_authoritative": False,
        "active_ownership_verified": False,
        "required_ci_verified": False,
        "issue_fix_verified": False,
        "claim_authorized": False, "merge_authorized": False,
        "retry_authorized": False, "deploy_authorized": False,
        "actions_executed": False,
        "issues": chosen,
    }


def markdown(report):
    lines = [
        "# zSentinel — live GitHub issue inbox",
        "",
        "**Read-only and advisory. Never authorizes a claim, retry, merge or deploy.**",
        "",
        "- Repository: " + report["repository"],
        "- Observed: " + report["observed_at_utc"],
        "- Open-issue source pages: " + str(report["pages_read"]) +
        "/" + str(report["max_pages"]),
        "- Source rows scanned: " + str(report["source_rows_scanned"]),
        "- Provider reported open issues: " +
        str(
            report["provider_total_issue_count_at_observation"]
            if report["provider_total_issue_count_at_observation"] is not None
            else "UNKNOWN"
        ),
        "- GitHub search incomplete results: " +
        str(report["provider_incomplete_results"]),
        "- Issues visible: " + str(report["total_issues_seen"]),
        "- Issues displayed: " + str(report["displayed_issues"]),
        "- Source partial/unknown: " + str(report["source_partial_or_unknown"]),
        "- Output truncated: " + str(report["display_truncated"]),
        "- GitHub GET attempts: " + str(report["get_attempts"]),
        "- Provider gap: " + (report["source_error"] or "none"),
        "- Explicit P0/P1 issues with no GitHub issue update for 72h: " +
        str(report["explicit_p0_p1_without_github_issue_update_72h"]),
        "- Age of GitHub issue update does NOT prove a stalled worker",
        "- Explicit label priority is advisory; title prefix hints are unverified",
        "- Missing priority is not evidence of no P0/P1 incidents",
        "- Independent owner/required CI/current repair: UNKNOWN",
        "",
        "## Current issue links, P0/P1-first",
        "",
    ]
    for row in report["issues"]:
        label = row["priority_from_explicit_labels"]
        hint = row["unverified_title_prefix_hint"]
        lines.append(
            "- [Issue #" + str(row["number"]) + "](" + row["url"] +
            ") · label " + label + " · unverified title hint " + hint +
            " · last issue update " + (row["updated_at_utc"] or "UNKNOWN") +
            " · no GitHub issue update for 72h: " +
            str(row["no_github_issue_update_for_72h"]) +
            " · owner/repair/merge UNKNOWN"
        )
        lines.append(
            "  - Next: `" + row["followup_command"] + "` "
            "(not executed)"
        )
    if not report["issues"]:
        lines.append(
            "- No issues observed in this bounded source window. "
            "This does NOT prove that no incidents exist."
        )
    lines.extend([
        "",
        "Review an issue without a saved dossier using "
        "`--live-issue-url <issue URL>` in the same zSentinel CLI.",
        "",
    ])
    return "\n".join(lines)


def html_view(report):
    esc = lambda v: html.escape(str(v), quote=True)
    rows = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; "
        "style-src 'unsafe-inline'; base-uri 'none'; form-action 'none';\">",
        "<title>zSentinel | GitHub issue inbox</title>",
        "<style>body{max-width:980px;margin:auto;padding:2rem;font:16px/1.5 "
        "system-ui,sans-serif;color:#203049;background:#f4f6fa}"
        "header,section{background:white;padding:1.25rem;margin:1rem 0;"
        "border-radius:.55rem;border:1px solid #bec9d5}"
        "header{border-left:5px solid #b06b16}a{color:#064f91}"
        "a:focus-visible{outline:3px solid #064f91}"
        "li{padding:.5rem 0;overflow-wrap:anywhere}"
        "small{color:#4d6175}code{overflow-wrap:anywhere}</style></head><body>",
        "<header><h1>zSentinel — current GitHub issue inbox</h1>",
        "<p><strong>Read-only navigation, never merge or claim permission.</strong>"
        " Priority comes from explicit source labels only; textual title "
        "prefixes are unverified navigation hints.</p>",
        "<p>Repository: <code>" + esc(report["repository"]) +
        "</code> · observed: " + esc(report["observed_at_utc"]) + "</p>",
        "<p>Pages " + esc(report["pages_read"]) + "/" +
        esc(report["max_pages"]) + " · scanned rows " +
        esc(report["source_rows_scanned"]) + " · provider count " +
        esc(
            report["provider_total_issue_count_at_observation"]
            if report["provider_total_issue_count_at_observation"] is not None
            else "UNKNOWN"
        ) +
        " · incomplete provider results: " +
        esc(report["provider_incomplete_results"]) +
        " · issues observed " +
        esc(report["total_issues_seen"]) + " · displayed " +
        esc(report["displayed_issues"]) + "</p>",
        "<p>Explicit P0/P1 with no issue update for 72h: " +
        esc(report["explicit_p0_p1_without_github_issue_update_72h"]) +
        " · this is NOT proof of stalled workers.</p>",
        "<p>Source coverage partial/UNKNOWN: " +
        esc(report["source_partial_or_unknown"]) +
        " · output truncated: " + esc(report["display_truncated"]) +
        " · source error: " + esc(report["source_error"] or "none") + "</p>",
        "</header><main><section><h2>P0/P1-first recent issues</h2><ol>",
    ]
    for item in report["issues"]:
        rows.append(
            "<li><a rel='noopener noreferrer' href='" + esc(item["url"]) +
            "'>Issue #" + esc(item["number"]) + "</a>"
            " · source label <strong>" +
            esc(item["priority_from_explicit_labels"]) + "</strong>"
            " · unverified title prefix " +
            esc(item["unverified_title_prefix_hint"]) +
            " · last GitHub update " +
            esc(item["updated_at_utc"] or "UNKNOWN") +
            " · no issue update for 72h: " +
            esc(item["no_github_issue_update_for_72h"]) +
            "<br><small>GitHub issue inactivity does NOT prove worker stall. "
            "Worker ownership, actual fix, required CI, "
            "protected-main merge and production: UNKNOWN.</small>"
            "<br><code>" + esc(item["followup_command"]) +
            "</code> <small>(not executed)</small></li>"
        )
    if not report["issues"]:
        rows.append(
            "<li>No recent issue observed in the bounded window. "
            "Absence has NOT been verified.</li>"
        )
    rows.extend([
        "</ol></section><section><h2>Inspect one issue next</h2>",
        "<p>Use <code>--live-issue-url &lt;issue URL&gt;</code> with the "
        "existing zSentinel CLI for source-linked PR discovery. Neither "
        "this inbox nor a title hint authorizes ownership, retries, "
        "CI bypass, merge or deploy.</p></section></main></body></html>",
    ])
    return "\n".join(rows)

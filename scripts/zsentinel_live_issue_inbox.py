#!/usr/bin/env python3
"""zSentinel read-only GitHub issue inbox: bounded live P0/P1-first operator view.

The inbox observes public/private issues only where the caller has ordinary
GitHub GET access. It does not claim that missing priority labels mean a
healthy project, that a title hint is an official priority, or that repairs
are safe to merge. Neither titles nor issue bodies leave the in-memory read.
"""
from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from urllib.parse import quote

from zsentinel_pr_live_read import (
    GitHubReadError, MAX_DISCOVERY_BODY_BYTES, _limited_json, utc,
)

REPO = re.compile(r"Zennay/([A-Za-z0-9_.-]{1,100})\Z")
ISSUE = re.compile(
    r"https://github[.]com/Zennay/([A-Za-z0-9_.-]{1,100})/issues/([1-9][0-9]*)\Z"
)
MAX_PAGES = 3
PAGE_SIZE = 30
MAX_DISPLAY = 45
MAX_LABELS = 100
PRIORITIES = ("P0", "P1", "P2", "P3")
RECOGNIZED = {
    p for prefix in ("", "priority:", "priority/") for p in
    ("p0", "p1", "p2", "p3") for p in (prefix + p,)
}


def _safe_issue(item, repository):
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
    title = item.get("title")
    title_hint = (
        "P0" if isinstance(title, str) and
        re.match(r"^\s*P0\b(?:\s|[:\-])", title, re.IGNORECASE) else
        "P1" if isinstance(title, str) and
        re.match(r"^\s*P1\b(?:\s|[:\-])", title, re.IGNORECASE) else "NONE"
    )
    effective = verified_label
    if effective == "UNKNOWN":
        rank = 4 if title_hint == "P0" else 5 if title_hint == "P1" else 6
    else:
        rank = PRIORITIES.index(effective)

    return {
        "number": number, "url": url, "updated_at_utc": updated_at,
        "priority_from_explicit_labels": verified_label,
        "explicit_priority_label_conflict": conflict,
        "label_source_complete": labels_complete,
        "unverified_title_prefix_hint": title_hint,
        "triage_rank": rank,
        "state": "open",
        "source_type": "github_recent_open_issue",
        "current_issue_details_fetched": False,
        "repair_prs_checked": False,
        "issue_owner_claim_verified": False,
        "workflow_gate_verified": False,
        "claim_authorized": False,
        "merge_authorized": False,
        "deploy_authorized": False,
    }


def observe_repo_inbox(repo_full_name, *, token=None):
    """Read recent open issues of one canonical Zennay repo, never mutate."""
    if not isinstance(repo_full_name, str):
        raise GitHubReadError("inbox repository must be owner/name")
    match = REPO.fullmatch(repo_full_name)
    if match is None:
        raise GitHubReadError("inbox repository must be Zennay/<repo>")
    name = match[1]
    api = "/repos/Zennay/" + quote(name, safe="")
    issues, seen = [], set()
    total_rows = 0
    prs_excluded = 0
    invalid_excluded = 0
    source_error = None
    attempts = 0
    pages_read = 0
    last_page_short = False

    for page in range(1, MAX_PAGES + 1):
        try:
            attempts += 1
            payload = _limited_json(
                api + "/issues?state=open&per_page=" + str(PAGE_SIZE) +
                "&sort=updated&direction=desc&page=" + str(page),
                token=token, max_body_bytes=MAX_DISCOVERY_BODY_BYTES,
            )
        except GitHubReadError as exc:
            source_error = str(exc)
            break
        if not isinstance(payload, list) or len(payload) > PAGE_SIZE:
            source_error = "GitHub open issue page malformed or oversized"
            break
        pages_read += 1
        total_rows += len(payload)
        last_page_short = len(payload) < PAGE_SIZE
        for raw in payload:
            if isinstance(raw, dict) and "pull_request" in raw:
                prs_excluded += 1
                continue
            item = _safe_issue(raw, name)
            if item is None:
                invalid_excluded += 1
                continue
            if item["number"] in seen:
                continue
            seen.add(item["number"])
            issues.append(item)
        if last_page_short:
            break

    # Labels are evidence, textual title prefixes are navigation-only hints.
    # Equal priorities retain GitHub's updated order; no untrusted free text
    # is emitted in any output format.
    issues.sort(key=lambda x: x["triage_rank"])
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

    return {
        "schema_version": 1, "policy": "zsentinel-live-github-issue-inbox-v1",
        "repository": repo_full_name,
        "observed_at_utc": utc(),
        "observation_mode": "bounded_recent_github_open_issues",
        "source": "github_rest_issues_list",
        "pages_read": pages_read, "max_pages": MAX_PAGES,
        "items_per_page": PAGE_SIZE, "source_rows_scanned": total_rows,
        "pull_requests_excluded": prs_excluded,
        "invalid_or_inaccessible_rows_excluded": invalid_excluded,
        "total_issues_seen": len(issues), "displayed_issues": len(chosen),
        "display_truncated": len(issues) > MAX_DISPLAY,
        "source_window_full": not last_page_short and source_error is None,
        "source_partial_or_unknown": bool(source_error) or not last_page_short,
        "source_error": source_error,
        "get_attempts": attempts, "read_get_attempt_upper_bound": MAX_PAGES,
        "priority_breakdown_in_source_window": by_rank,
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
        "- Issues visible: " + str(report["total_issues_seen"]),
        "- Issues displayed: " + str(report["displayed_issues"]),
        "- Source partial/unknown: " + str(report["source_partial_or_unknown"]),
        "- Output truncated: " + str(report["display_truncated"]),
        "- GitHub GET attempts: " + str(report["get_attempts"]),
        "- Provider gap: " + (report["source_error"] or "none"),
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
            " · updated " + (row["updated_at_utc"] or "UNKNOWN") +
            " · owner/repair/merge UNKNOWN"
        )
    if not report["issues"]:
        lines.append(
            "- No issues observed in this bounded source window. "
            "This does NOT prove that no incidents exist."
        )
    lines.extend([
        "",
        "Review an issue without a saved dossier using "
        "\`--live-issue-url <issue URL>\` in the same zSentinel CLI.",
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
        esc(report["source_rows_scanned"]) + " · issues " +
        esc(report["total_issues_seen"]) + " · displayed " +
        esc(report["displayed_issues"]) + "</p>",
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
            "<br><small>Worker ownership, actual fix, required CI, "
            "protected-main merge and production: UNKNOWN.</small></li>"
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

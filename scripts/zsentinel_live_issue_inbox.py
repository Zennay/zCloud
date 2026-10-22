#!/usr/bin/env python3
"""zSentinel read-only GitHub issue inbox: bounded live P0/P1-first operator view.

The inbox observes public/private issues only where the caller has ordinary
GitHub GET access. It does not claim that missing priority labels mean a
healthy project, that a title hint is an official priority, or that repairs
are safe to merge. Neither titles nor issue bodies leave the in-memory read.
"""
from __future__ import annotations

import csv
import html
import io
import re
import unicodedata
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
MAX_SAMPLED_DISPLAY_PAGES = 5  # 3x30 recent + 4x30 recall <= 210 source rows
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
    label_title_disagreement = (
        verified_label in ("P0", "P1", "P2", "P3")
        and title_hint != "NONE" and title_hint != verified_label
    )
    # Explicit GitHub priority labels always govern this issue, including
    # P2/P3. A contradictory title prefix must not move a labelled P2/P3
    # into the P0/P1 navigation-hint groups. Title-only recall is allowed
    # only when the entire label source is trustworthy and has no priority.
    if verified_label == "P0":
        rank = 0
    elif verified_label == "P1":
        rank = 1
    elif verified_label == "P2":
        rank = 4
    elif verified_label == "P3":
        rank = 5
    elif labels_complete and not conflict and title_hint == "P0":
        rank = 2
    elif labels_complete and not conflict and title_hint == "P1":
        rank = 3
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
        "explicit_label_title_disagreement": label_title_disagreement,
        "triage_rank": rank,
        "source_window_identity_drift": False,
        "state": "open",
        "source_type": "github_recent_open_issue",
        "owner_next_step": (
            "inspect_conflicting_priority_with_issue_owner"
            if label_title_disagreement else
            "inspect_current_issue_and_existing_repair_prs"
        ),
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



def _operator_workflow(item):
    """One three-line, human-only owner route from source-admitted issue data.

    Source classification cannot establish a lease, real worker outage,
    approval, or effective severity outside the observed GitHub window.
    """
    if item["source_window_identity_drift"]:
        kind = "source_changed"
        problem = "GitHub issue evidence changed between sampled windows"
        action = "Reopen the original issue and re-confirm priority and active owner"
    elif item["explicit_priority_label_conflict"]:
        kind = "conflicting_priority_labels"
        problem = "Multiple GitHub priority labels conflict"
        action = "Ask the issue owner to reconcile the original priority labels"
    elif item["label_source_complete"] is False:
        kind = "label_source_unknown"
        problem = "GitHub priority labels were not completely observable"
        action = "Inspect original issue labels before assigning a priority"
    elif item["explicit_label_title_disagreement"] is True:
        kind = "label_title_conflict"
        problem = "Issue title and explicit GitHub priority label disagree"
        action = "Ask the issue owner to reconcile title and priority label"
    elif item["triage_rank"] in (0, 1):
        kind = "explicit_p0_p1"
        problem = "Open issue with source-observed P0/P1 priority label"
        action = "Inspect this issue and existing repair PRs; verify active owner"
    elif item["triage_rank"] in (2, 3):
        kind = "title_hint_only"
        problem = "Issue title suggests P0/P1; no matching label observed"
        action = "Confirm priority with issue owner before promoting urgency"
    else:
        kind = "other_or_unknown"
        problem = "Open issue in bounded source with lower or unknown priority"
        action = "Inspect the source issue and existing repair PRs"
    codes = {
        "source_changed": "recheck_changed_issue_source_before_owner_action",
        "conflicting_priority_labels": "reconcile_conflicting_priority_labels_with_issue_owner",
        "label_source_unknown": "inspect_source_issue_labels_before_priority",
        "label_title_conflict": "inspect_conflicting_priority_with_issue_owner",
        "explicit_p0_p1": "inspect_current_issue_and_existing_repair_prs",
        "title_hint_only": "confirm_title_priority_with_issue_owner",
        "other_or_unknown": "inspect_current_issue_and_existing_repair_prs",
    }
    return {
        "workstream": kind,
        "human_action_code": codes[kind],
        "problem": problem,
        "action": action,
        "status": "SOURCE OBSERVED; owner/CI/merge/recovery NOT VERIFIED",
        "human_followup_only": True,
        "issue_url": item["url"],
        "owner_claim_verified": False,
        "automatic_recovery_started": False,
        "independent_review_verified": False,
        "merge_authorized": False,
        "deploy_authorized": False,
    }


def observe_repo_inbox(repo_full_name, *, token=None, display_page=1,
                       display_all_sample=False):
    """Recent issue inventory plus bounded P0/P1 recall when recent window ends.

    The issue-only search preserves original GitHub source ordering; the
    result is still a bounded snapshot, not an exhaustive owner assignment.
    P0/P1 title recall is a navigation hint and must never grant authority.
    """
    if type(display_page) is not int or not 1 <= display_page <= MAX_SAMPLED_DISPLAY_PAGES:
        raise GitHubReadError("inbox display page must be 1 through 5")
    if type(display_all_sample) is not bool or (
            display_all_sample and display_page != 1):
        raise GitHubReadError("full sampled export cannot select a display page")
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
    recent_provider_count_drift = False
    recent_rows_read = 0
    recent_duplicate_issue_records = 0
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
        nonlocal recent_duplicate_issue_records
        total_rows += len(items)
        for raw in items:
            if isinstance(raw, dict) and "pull_request" in raw:
                prs_excluded += 1
                continue
            item = _safe_issue(raw, name, observed_at=observed_at)
            if item is None:
                invalid_excluded += 1
                continue
            if source.startswith("label_"):
                # Cross-check the actual returned labels; provider search
                # results alone do not establish priority or ownership.
                if item["priority_from_explicit_labels"] != source[-2:]:
                    continue
            elif source != "recent" and (
                    item["unverified_title_prefix_hint"] != source
                    and item["priority_from_explicit_labels"] != source):
                # Text queries may match words anywhere, not just prefixes.
                continue
            existing = collected.get(item["number"])
            if existing is None:
                item["inbox_source_windows"] = [source]
                collected[item["number"]] = item
            else:
                if source == "recent":
                    recent_duplicate_issue_records += 1
                if source not in existing["inbox_source_windows"]:
                    existing["inbox_source_windows"].append(source)
                if (existing["updated_at_utc"] != item["updated_at_utc"]
                        or existing["priority_from_explicit_labels"] !=
                            item["priority_from_explicit_labels"]
                        or existing["unverified_title_prefix_hint"] !=
                            item["unverified_title_prefix_hint"]):
                    # GitHub result pages are not an atomic snapshot.
                    # Drift means the original derived priority is no longer
                    # reliable and cannot remain prioritized as a P0.
                    existing["source_window_identity_drift"] = True
                    existing["owner_next_step"] = (
                        "recheck_changed_issue_source_before_owner_action"
                    )
                    existing["explicit_label_title_disagreement"] = "UNKNOWN"
                    existing["priority_from_explicit_labels"] = "UNKNOWN"
                    existing["unverified_title_prefix_hint"] = "NONE"
                    existing["triage_rank"] = 6
                    existing["no_github_issue_update_for_72h"] = "UNKNOWN"
                    existing["last_github_update_age_hours"] = None

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
        recent_rows_read += len(items)
        if provider_total_count is not None and provider_total_count != count:
            recent_provider_count_drift = True
        provider_total_count = count
        provider_incomplete = provider_incomplete or incomplete
        admit_rows(items, "recent")
        # A short GitHub Search page does not prove the result set ended
        # when total_count still reports more issues. Keep the bounded
        # pagination active, and then recall older high-priority items.
        recent_window_truncated = recent_rows_read < count
        if not recent_window_truncated:
            break

    # When all recent open issues fit, the P0/P1 search is redundant.
    # When the recent window is incomplete, independently recall old
    # explicit-label P0/P1 cases and title-only P0/P1 hints. Four strictly
    # bounded first pages (30 records each) are optional and advisory.
    # No title hit becomes authoritative unless the returned issue itself
    # has one single explicit P0/P1 label.
    if source_error is None and (
        recent_window_truncated or provider_incomplete
        or recent_provider_count_drift
        or recent_duplicate_issue_records or invalid_excluded
    ):
        recall_performed = True
        scopes = [
            ("label_P0", "label:P0,priority:P0,priority/P0",
             "explicit_label"),
            ("label_P1", "label:P1,priority:P1,priority/P1",
             "explicit_label"),
            ("P0", "P0 in:title", "unverified_title"),
            ("P1", "P1 in:title", "unverified_title"),
        ]
        for source, query_suffix, kind in scopes:
            try:
                items, count, incomplete = read_page(
                    query_prefix + " " + query_suffix, 1
                )
            except GitHubReadError as exc:
                source_error = str(exc)
                break
            provider_incomplete = provider_incomplete or incomplete
            recall_sources.append({
                "priority": source[-2:],
                "source_kind": kind,
                "source_matches": count,
                "sampled_rows": len(items),
                "truncated": len(items) == PAGE_SIZE and count > PAGE_SIZE,
                "provider_incomplete": incomplete,
                "authority": False,
            })
            admit_rows(items, source)

    issues = list(collected.values())
    for issue in issues:
        issue["operator_workflow"] = _operator_workflow(issue)
        # Preserve the existing field for older source consumers, but make
        # it agree with the current (possibly drifted) source observation.
        issue["owner_next_step"] = issue["operator_workflow"]["human_action_code"]
    issues.sort(key=lambda x: (
        x["triage_rank"],
        0 if x["no_github_issue_update_for_72h"] is True else 1,
        -(x["last_github_update_age_hours"] or 0),
    ))
    start = (display_page - 1) * MAX_DISPLAY
    chosen = issues if display_all_sample else issues[start:start + MAX_DISPLAY]
    sampled_display_pages = max(
        1, (len(issues) + MAX_DISPLAY - 1) // MAX_DISPLAY
    )
    source_owner_workstreams = {}
    for item in issues:
        category = item["operator_workflow"]["workstream"]
        source_owner_workstreams[category] = (
            source_owner_workstreams.get(category, 0) + 1
        )
    by_rank = {}
    for item in issues:
        label = (
            item["priority_from_explicit_labels"]
            if item["priority_from_explicit_labels"] != "UNKNOWN"
            else "TITLE_HINT_" + item["unverified_title_prefix_hint"]
            if item["triage_rank"] in (2, 3)
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
        "recent_rows_read": recent_rows_read,
        "recent_duplicate_issue_records": recent_duplicate_issue_records,
        "pull_requests_excluded": prs_excluded,
        "provider_total_issue_count_at_observation": provider_total_count,
        "provider_incomplete_results": provider_incomplete,
        "recent_provider_count_drift": recent_provider_count_drift,
        "issue_rows_with_cross_window_drift": sum(
            item["source_window_identity_drift"] for item in issues
        ),
        "recent_window_truncated": recent_window_truncated,
        "recent_issue_window_complete": (
            pages_read > 0 and source_error is None
            and not provider_incomplete and not recent_provider_count_drift
            and not recent_window_truncated
            and not recent_duplicate_issue_records
            and not invalid_excluded
        ),
        "priority_recall_performed": recall_performed,
        "priority_recall_sources": recall_sources,
        "priority_recall_not_authoritative": True,
        "invalid_or_inaccessible_rows_excluded": invalid_excluded,
        "total_issues_seen": len(issues), "displayed_issues": len(chosen),
        "display_truncated": len(issues) > len(chosen),
        "all_sampled_issues_selected": display_all_sample,
        "display_page": display_page,
        "display_pages_in_sample": sampled_display_pages,
        "display_page_unavailable_in_sample": (
            display_page > sampled_display_pages
        ),
        "display_omitted_sample_issues": len(issues) - len(chosen),
        "display_has_next_sample_page": (
            not display_all_sample and display_page < sampled_display_pages
        ),
        "display_has_previous_sample_page": (
            not display_all_sample and display_page > 1
        ),
        # "full" means the recent issue inventory actually finished with
        # stable coverage, not that the three-page budget was exhausted.
        "source_window_full": (
            pages_read > 0 and source_error is None
            and not provider_incomplete and not recent_provider_count_drift
            and not recent_window_truncated
            and not recent_duplicate_issue_records
            and not invalid_excluded
        ),
        "source_partial_or_unknown": (
            bool(source_error) or provider_incomplete or recent_provider_count_drift
            or recent_window_truncated or recall_partial
            or recent_duplicate_issue_records > 0 or invalid_excluded > 0
            or any(item["source_window_identity_drift"] for item in issues)
        ),
        "source_error": source_error,
        "get_attempts": attempts,
        "read_get_attempt_upper_bound": MAX_PAGES + 4,
        "priority_breakdown_in_source_window": by_rank,
        "operator_workstreams_in_bounded_source": source_owner_workstreams,
        "operator_workflows_are_human_only": True,
        "observed_label_title_disagreements": sum(
            item["explicit_label_title_disagreement"] is True
            for item in issues
        ),
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
        "- Recent open-issue pages: " + str(report["pages_read"]) +
        "/" + str(report["max_pages"]),
        "- Recent window truncated: " +
        str(report["recent_window_truncated"]),
        "- Recent issue window complete: " +
        str(report["recent_issue_window_complete"]),
        "- Recent rows read: " + str(report["recent_rows_read"]),
        "- Duplicate recent issue records: " +
        str(report["recent_duplicate_issue_records"]),
        "- Extra P0/P1 title recall performed: " +
        str(report["priority_recall_performed"]),
        "- Maximum bounded GETs: " +
        str(report["read_get_attempt_upper_bound"]),
        "- Source rows scanned: " + str(report["source_rows_scanned"]),
        "- Provider reported open issues: " +
        str(
            report["provider_total_issue_count_at_observation"]
            if report["provider_total_issue_count_at_observation"] is not None
            else "UNKNOWN"
        ),
        "- GitHub search incomplete results: " +
        str(report["provider_incomplete_results"]),
        "- Recent source total-count changed during pagination: " +
        str(report["recent_provider_count_drift"]),
        "- Issues with conflicting source-window observations: " +
        str(report["issue_rows_with_cross_window_drift"]),
        "- P0/P1 title matches are navigation-only, not verified priority",
        "- Explicit label/title contradictions: " +
        str(report["observed_label_title_disagreements"]),
        "- Owner workstreams in bounded sample: " +
        ", ".join(k + "=" + str(v) for k, v in
                  report["operator_workstreams_in_bounded_source"].items()),
        "- Issues visible: " + str(report["total_issues_seen"]),
        "- Issues displayed: " + str(report["displayed_issues"]),
        "- Selected sampled page: " + str(report["display_page"]) +
        "/" + str(report["display_pages_in_sample"]),
        "- Other sampled issues excluded from this page: " +
        str(report["display_omitted_sample_issues"]),
        "- Selected page outside current bounded sample: " +
        str(report["display_page_unavailable_in_sample"]),
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
    if report["priority_recall_performed"]:
        lines.extend([
            "### Targeted older P0/P1 issue recalls",
            "",
        ])
        for scope in report["priority_recall_sources"]:
            lines.append(
                "- " + scope["priority"] + " " +
                scope["source_kind"] + " search: "
                + str(scope["source_matches"]) + " provider matches, "
                + str(scope["sampled_rows"]) + " sampled, truncated="
                + str(scope["truncated"]) + ", provider incomplete="
                + str(scope["provider_incomplete"]) + "; owner/priority UNKNOWN"
            )
        lines.append("")
    for row in report["issues"]:
        label = row["priority_from_explicit_labels"]
        hint = row["unverified_title_prefix_hint"]
        lines.append(
            "- [Issue #" + str(row["number"]) + "](" + row["url"] +
            ") · label " + label + " · unverified title hint " + hint +
            " · label/title disagreement: " +
            str(row["explicit_label_title_disagreement"]) +
            " · next owner step: " + row["owner_next_step"] +
            " · last issue update " + (row["updated_at_utc"] or "UNKNOWN") +
            " · no GitHub issue update for 72h: " +
            str(row["no_github_issue_update_for_72h"]) +
            " · inbox sources: " +
            ",".join(row["inbox_source_windows"]) +
            " · conflicting source snapshots: " +
            str(row["source_window_identity_drift"]) +
            " · owner/repair/merge UNKNOWN"
        )
        workflow = row["operator_workflow"]
        lines.extend([
            "  - **Problem:** " + workflow["problem"],
            "  - **Action:** " + workflow["action"] +
            " (open original issue; no automatic recovery)",
            "  - **Status:** " + workflow["status"],
            "  - Followup command: `" + row["followup_command"] + "` (not executed)",
        ])
    if not report["issues"]:
        lines.append(
            "- No issues observed on selected page of this bounded source. "
            "Other sampled pages may still contain incidents; this does "
            "NOT prove that no incidents exist."
        )
    lines.extend([
        "",
        "Review an issue without a saved dossier using "
        "`--live-issue-url <issue URL>` in the same zSentinel CLI.",
        "",
    ])
    return "\n".join(lines)



def workorders_csv(report, *, compact_all=False):
    """Private, bounded source-only operator handoff; not an execution queue."""
    rows = io.StringIO(newline="")
    writer = csv.writer(rows, lineterminator=chr(10))
    headings = (
        "observed_at_utc", "repository", "display_page",
        "sampled_pages", "issue_url", "priority_source",
        "unverified_title_hint", "source_window_drift",
        "priority_label_conflict", "label_title_disagreement",
        "operator_workstream", "owner_step_code", "problem", "human_action", "status",
        "issue_updated_at_utc", "source_windows", "recent_window_complete",
        "source_partial_or_unknown", "display_truncated",
        "source_issues_seen", "issue_claim_verified", "required_ci_verified",
        "merge_authorized", "deploy_authorized",
    )
    if not compact_all:
        writer.writerow(headings)

    def safe(value):
        if isinstance(value, bool):
            return str(value).lower()
        if value is None:
            return ""
        field = str(value)
        # CSV quoting alone does not prevent formula interpretation. Untrusted
        # whitespace/format prefixes must not disguise a leading operator.
        i = 0
        while i < len(field) and (
                field[i].isspace() or
                unicodedata.category(field[i]) in ("Cf", "Zs", "Zl", "Zp")):
            i += 1
        if field[i:].startswith(("=", "+", "-", "@")):
            return "'" + field
        return field

    if compact_all:
        # All sampled issues come from ONE provider read, not independent
        # page reads with moving search boundaries. Compact source-only
        # columns keep the largest 210-item sample within the 128-KiB
        # private output ceiling; user text and worker contacts stay absent.
        if report["all_sampled_issues_selected"] is not True:
            raise ValueError("all-sample CSV requires full bounded source")
        writer.writerow((
            "observed_at_utc", "repository", "issue_url", "label_priority",
            "human_action_code", "human_action", "source_conflict",
            "sampled_issue_count", "source_partial_or_unknown",
            "active_owner_verified", "main_merge_authorized",
        ))
        for item in report["issues"]:
            step = item["operator_workflow"]
            writer.writerow([safe(value) for value in (
                report["observed_at_utc"], report["repository"], item["url"],
                item["priority_from_explicit_labels"],
                step["human_action_code"], step["action"],
                item["source_window_identity_drift"],
                report["total_issues_seen"],
                report["source_partial_or_unknown"], False, False,
            )])
        if not report["issues"]:
            writer.writerow([safe(value) for value in (
                report["observed_at_utc"], report["repository"], "", "UNKNOWN",
                "inspect_current_source_before_owner_action",
                "No issues in bounded source; verify current GitHub issues",
                False, 0, report["source_partial_or_unknown"], False, False,
            )])
        return rows.getvalue()

    for item in report["issues"]:
        workflow = item["operator_workflow"]
        writer.writerow([safe(value) for value in (
            report["observed_at_utc"], report["repository"],
            report["display_page"], report["display_pages_in_sample"],
            item["url"], item["priority_from_explicit_labels"],
            item["unverified_title_prefix_hint"],
            item["source_window_identity_drift"],
            item["explicit_priority_label_conflict"],
            item["explicit_label_title_disagreement"],
            workflow["workstream"], workflow["human_action_code"],
            workflow["problem"], workflow["action"],
            workflow["status"], item["updated_at_utc"],
            ";".join(item["inbox_source_windows"]),
            report["recent_issue_window_complete"],
            report["source_partial_or_unknown"], report["display_truncated"],
            report["total_issues_seen"], False, False, False, False,
        )])
    if not report["issues"]:
        # Preserve original source custody instead of an easily
        # misinterpreted header-only CSV.
        writer.writerow([safe(value) for value in (
            report["observed_at_utc"], report["repository"],
            report["display_page"], report["display_pages_in_sample"],
            "", "UNKNOWN", "NONE", False, False, "UNKNOWN", "metadata_only_no_issues",
            "inspect_source_before_owner_action",
            "No issues observed in bounded sampled source",
            "Refresh source and independently inspect current open issues",
            "SOURCE SAMPLE EMPTY; absence of incidents NOT VERIFIED",
            "", "", report["recent_issue_window_complete"],
            report["source_partial_or_unknown"], False, 0,
            False, False, False, False,
        )])
    return rows.getvalue()


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
        esc(report["displayed_issues"]) + " · sampled page " +
        esc(report["display_page"]) + "/" +
        esc(report["display_pages_in_sample"]) + " · other sample rows " +
        esc(report["display_omitted_sample_issues"]) + "</p>",
        "<p>Observed explicit label/title priority disagreements: " +
        esc(report["observed_label_title_disagreements"]) +
        " · reconcile with the issue owner; never silently promote a " +
        "title hint over a label.</p>",
        "<p>Source owner routes: " +
        esc(", ".join(k + "=" + str(v) for k, v in
                      report["operator_workstreams_in_bounded_source"].items())) +
        " · all human-only, all without verified ownership.</p>",
        "<p>Explicit P0/P1 with no issue update for 72h: " +
        esc(report["explicit_p0_p1_without_github_issue_update_72h"]) +
        " · this is NOT proof of stalled workers.</p>",
        "<p>Recent inventory truncated: " +
        esc(report["recent_window_truncated"]) +
        " · targeted older P0/P1 recall: " +
        esc(report["priority_recall_performed"]) +
        " · bounded GET attempts: " + esc(report["get_attempts"]) +
        "/" + esc(report["read_get_attempt_upper_bound"]) + "</p>",
        "<p>Recent window complete: " +
        esc(report["recent_issue_window_complete"]) +
        " · recent rows read: " + esc(report["recent_rows_read"]) +
        " · duplicate recent issue records: " +
        esc(report["recent_duplicate_issue_records"]) + "</p>",
        "<p>Recent source count drift: " +
        esc(report["recent_provider_count_drift"]) +
        " · issues with conflicting snapshots: " +
        esc(report["issue_rows_with_cross_window_drift"]) + "</p>",
        "<p>Source coverage partial/UNKNOWN: " +
        esc(report["source_partial_or_unknown"]) +
        " · output truncated: " + esc(report["display_truncated"]) +
        " · source error: " + esc(report["source_error"] or "none") + "</p>",
        "</header><main>",
    ]
    if report["priority_recall_performed"]:
        rows.append(
            "<section><h2>Older P0/P1 label and title recall</h2>"
            "<p>Only issue labels verified in the returned issue source "
            "establish advisory P0/P1 priority. Title-only hits remain "
            "unverified. Neither establishes worker ownership, approved "
            "recovery or a deployed repair.</p><ul>"
        )
        for scope in report["priority_recall_sources"]:
            rows.append(
                "<li>" + esc(scope["priority"]) + " (" +
                esc(scope["source_kind"]) + "): " +
                esc(scope["source_matches"]) + " source matches, " +
                esc(scope["sampled_rows"]) + " sampled, truncated " +
                esc(scope["truncated"]) + ", provider incomplete " +
                esc(scope["provider_incomplete"]) + "</li>"
            )
        rows.append("</ul></section>")
    priority_groups = (
        ("p0", "Explicit P0-labelled issues", lambda rank: rank == 0),
        ("p1", "Explicit P1-labelled issues", lambda rank: rank == 1),
        ("hint", "Unverified P0/P1 title hints", lambda rank: rank in (2, 3)),
        ("lower", "Explicit P2/P3-labelled issues", lambda rank: rank in (4, 5)),
        ("unknown", "Unknown or conflicting source priority", lambda rank: rank == 6),
    )
    grouped = [(key, title, [
        item for item in report["issues"] if match(item["triage_rank"])
    ]) for key, title, match in priority_groups]
    rows.append("<nav aria-label='Issue priority groups'><p>Jump to observed issue groups: ")
    rows.append(" · ".join(
        "<a href='#" + key + "'>" + esc(title) + " (" +
        str(len(items)) + ")</a>"
        for key, title, items in grouped if items
    ) or "No issue rows observed")
    rows.append("</p></nav>")
    for key, title, items in grouped:
        if not items:
            continue
        rows.append("<section id='" + key + "'><h2>" + esc(title) +
                    " (" + str(len(items)) + ")</h2><ol>")
        for item in items:
            workflow = item["operator_workflow"]
            rows.append(
            "<li><a rel='noopener noreferrer' href='" + esc(item["url"]) +
            "'>Issue #" + esc(item["number"]) + "</a>"
            " · source label <strong>" +
            esc(item["priority_from_explicit_labels"]) + "</strong>"
            " · unverified title prefix " +
            esc(item["unverified_title_prefix_hint"]) +
            " · priority disagreement: " +
            esc(item["explicit_label_title_disagreement"]) +
            " · human next step: " + esc(item["owner_next_step"]) +
            " · last GitHub update " +
            esc(item["updated_at_utc"] or "UNKNOWN") +
            " · no issue update for 72h: " +
            esc(item["no_github_issue_update_for_72h"]) +
            " · discovered in: " +
            esc(", ".join(item["inbox_source_windows"])) +
            " · source changed during read: " +
            esc(item["source_window_identity_drift"]) +
            "<p><strong>Problem:</strong> " + esc(workflow["problem"]) +
            "<br><strong>Action:</strong> " + esc(workflow["action"]) +
            "<br><strong>Status:</strong> " + esc(workflow["status"]) +
            "</p><small>GitHub issue inactivity does NOT prove worker stall. "
            "Worker ownership, actual fix, required CI, "
            "protected-main merge and production: UNKNOWN.</small>"
            "<br><code>" + esc(item["followup_command"]) +
            "</code> <small>(not executed)</small></li>"
        )
        rows.append("</ol></section>")
    if not report["issues"]:
        rows.append(
            "<section><p>No issue observed on this selected bounded page. "
            "Other pages may contain issues. Absence has NOT been "
            "verified.</p></section>"
        )
    rows.extend([
        "<section><h2>Inspect one issue next</h2>",
        "<p>Use <code>--live-issue-url &lt;issue URL&gt;</code> with the "
        "existing zSentinel CLI for source-linked PR discovery. Neither "
        "this inbox nor a title hint authorizes ownership, retries, "
        "CI bypass, merge or deploy.</p></section></main></body></html>",
    ])
    return "\n".join(rows)

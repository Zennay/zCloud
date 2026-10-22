#!/usr/bin/env python3
"""zSentinel multi-project GitHub issue desk: source-bound human-only routing.

Up to six explicit Zennay repositories, each using the existing bounded
issue-only GitHub reader. Never claims workers, dispatches actions or grants
merge authority. Repository observations are sequential, not atomic.
"""
from __future__ import annotations

import argparse
import csv
import html
import io
import json
import os
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from zsentinel_live_issue_inbox import REPO, observe_repo_inbox
from zsentinel_pr_live_read import GitHubReadError
from zsentinel_recovery_policy import (
    MAX_OUTPUT_BYTES, PolicyInputError, _emit_output,
)

MAX_REPOS = 6
MAX_GLOBAL_CARDS = 120
ALL_RANKS = range(7)
VIEWS = {
    "all": frozenset(ALL_RANKS),
    "explicit-critical": frozenset((0, 1)),
    "critical-and-title-hints": frozenset((0, 1, 2, 3)),
}


def observe_portfolio(repositories, *, token=None, view="all"):
    """Read each explicitly selected repository once; never infer an absent issue."""
    if type(view) is not str or view not in VIEWS:
        raise ValueError("unknown bounded display view")
    if not isinstance(repositories, list) or not 2 <= len(repositories) <= MAX_REPOS:
        raise ValueError("choose 2–6 canonical Zennay repositories")
    if (any(type(name) is not str or REPO.fullmatch(name) is None
            for name in repositories) or len(set(repositories)) != len(repositories)):
        raise ValueError("repositories must be distinct canonical Zennay/name values")
    recorded = []
    ranked = {}
    stopped_for_provider = False
    all_priority_counts = {
        "label_p0": 0, "label_p1": 0,
        "unverified_title_hint": 0, "p2_p3": 0, "unknown": 0,
    }
    for name in repositories:
        if stopped_for_provider:
            recorded.append({
                "repository": name, "state": "NOT_READ_AFTER_SOURCE_ERROR",
                "observed_at_utc": None, "sampled_count": None,
                "recent_window_complete": False, "source_partial_or_unknown": True,
                "provider_issue_count": None, "get_attempts": 0,
                "source_error": "previous_repo_source_unavailable",
                "unknown_issue_absence": True,
                "source_priority_counts": None,
            })
            continue
        try:
            source = observe_repo_inbox(
                name, token=token, display_all_sample=True,
            )
        except GitHubReadError:
            # A provider authorization/rate-limit error may be raised
            # *before* the first source page is returned. Preserve prior
            # projects in the private handoff, stop future API calls, and
            # never classify the unread repository as having no incidents.
            recorded.append({
                "repository": name, "state": "SOURCE_UNAVAILABLE",
                "observed_at_utc": None, "sampled_count": None,
                "recent_window_complete": False,
                "source_partial_or_unknown": True,
                "provider_issue_count": None,
                "get_attempts": None,
                "source_error": "github_source_read_failed",
                "unknown_issue_absence": True,
                "source_priority_counts": None,
            })
            stopped_for_provider = True
            continue
        error = source["source_error"]
        rank_totals = {
            "label_p0": sum(issue["triage_rank"] == 0 for issue in source["issues"]),
            "label_p1": sum(issue["triage_rank"] == 1 for issue in source["issues"]),
            "unverified_title_hint": sum(
                issue["triage_rank"] in (2, 3) for issue in source["issues"]
            ),
            "p2_p3": sum(
                issue["triage_rank"] in (4, 5) for issue in source["issues"]
            ),
            "unknown": sum(issue["triage_rank"] == 6 for issue in source["issues"]),
        }
        for category, count in rank_totals.items():
            all_priority_counts[category] += count
        recorded.append({
            "repository": name,
            "state": "INCOMPLETE" if error or source["source_partial_or_unknown"]
            else "SAMPLED",
            "observed_at_utc": source["observed_at_utc"],
            "sampled_count": source["total_issues_seen"],
            "recent_window_complete": source["recent_issue_window_complete"],
            "source_partial_or_unknown": source["source_partial_or_unknown"],
            "provider_issue_count": source["provider_total_issue_count_at_observation"],
            "get_attempts": source["get_attempts"],
            "source_error": "github_source_read_failed" if error else None,
            "unknown_issue_absence": True,
            "source_priority_counts": rank_totals,
        })
        rows = {}
        for priority in ALL_RANKS:
            rows[priority] = [
                item for item in source["issues"]
                if item["triage_rank"] == priority
            ]
        ranked[name] = rows
        if error:
            # Fail closed rather than hammering an account for which GitHub
            # may have refused credentials, quota or external admission.
            stopped_for_provider = True

    # A project without readable GitHub source is a real operator
    # dependency, not an empty or lower-priority incident backlog.
    source_routes = {
        "SOURCE_UNAVAILABLE": (
            "confirm_github_access_or_provider_admission",
            "Confirm GitHub authorization, repository access and quota "
            "with the source owner before repeating this read.",
        ),
        "NOT_READ_AFTER_SOURCE_ERROR": (
            "retry_source_only_after_upstream_access_restored",
            "Resume this repository's source read only after the earlier "
            "GitHub admission blocker is resolved.",
        ),
        "INCOMPLETE": (
            "inspect_partial_github_issue_source",
            "Review observed issue links and the truncated/incomplete "
            "GitHub source windows; unobserved issues remain UNKNOWN.",
        ),
        "SAMPLED": (
            "inspect_existing_prs_and_current_worker_claims",
            "Review current issue and existing repair PRs; establish "
            "active owner and exact-head gates before any action.",
        ),
    }
    for observation in recorded:
        code, action = source_routes[observation["state"]]
        observation["human_source_followup_code"] = code
        observation["human_source_followup"] = action
        observation["human_source_followup_executed"] = False
        observation["source_owner_verified"] = False

    # P0/P1 first ACROSS repositories, round-robin for project fairness.
    # This cannot promote a title hint above an actual source label.
    selected = []
    for priority in sorted(VIEWS[view]):
        while len(selected) < MAX_GLOBAL_CARDS:
            pending = False
            for name in repositories:
                if name not in ranked:
                    continue
                bucket = ranked[name][priority]
                if not bucket:
                    continue
                pending = True
                issue = bucket.pop(0)
                workflow = issue["operator_workflow"]
                selected.append({
                    "repository": name,
                    "url": issue["url"],
                    "number": issue["number"],
                    "priority_rank": priority,
                    "explicit_priority": issue["priority_from_explicit_labels"],
                    "title_hint_unverified": issue["unverified_title_prefix_hint"],
                    "issue_updated_at_utc": issue["updated_at_utc"],
                    "source_window_changed": issue["source_window_identity_drift"],
                    "source_label_title_conflict": issue["explicit_label_title_disagreement"],
                    "human_action_code": workflow["human_action_code"],
                    "problem": workflow["problem"],
                    "human_next_action": workflow["action"],
                    "status": workflow["status"],
                    "single_issue_live_pr_review_command": issue["followup_command"],
                    "live_pr_review_command_executed": False,
                    "claim_authorized": False,
                    "required_ci_verified": False,
                    "merge_authorized": False,
                    "deploy_authorized": False,
                })
                if len(selected) >= MAX_GLOBAL_CARDS:
                    break
            if not pending:
                break
        if len(selected) >= MAX_GLOBAL_CARDS:
            break

    source_sampled = sum((r["sampled_count"] or 0) for r in recorded)
    # Unknown failed-request counts must not be misrepresented as a
    # measured total. The absolute budget still holds across 2–6 sources.
    attempts_known = all(
        type(r["get_attempts"]) is int for r in recorded
    )
    actual_gets = (
        sum(r["get_attempts"] for r in recorded)
        if attempts_known else None
    )
    eligible_count = sum(
        len([issue for issue in ranked.get(repo, {}).get(priority, [])
             if issue["triage_rank"] == priority])
        for repo in repositories for priority in VIEWS[view]
    ) + len(selected)
    source_gap = (
        stopped_for_provider or any(
            item["source_partial_or_unknown"] for item in recorded
        )
    )
    display_gap = source_sampled > len(selected)
    return {
        "schema_version": 1,
        "policy": "zsentinel-portfolio-github-issue-desk-v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source_observations_are_not_atomic_across_repositories": True,
        "repositories_requested": repositories,
        "repositories_observed": recorded,
        "requested_count": len(repositories),
        "read_count": sum(x["observed_at_utc"] is not None for x in recorded),
        "provider_stopped_early": stopped_for_provider,
        "bounded_get_attempts": actual_gets,
        "bounded_get_attempts_unknown": not attempts_known,
        "bounded_get_ceiling": 7 * len(repositories),
        "total_sampled_issues": source_sampled,
        "source_priority_counts": all_priority_counts,
        "display_view": view,
        "total_eligible_in_view": eligible_count,
        "total_displayed_issues": len(selected),
        "omitted_due_to_display_limit": eligible_count - len(selected),
        "filtered_out_by_view": source_sampled - eligible_count,
        "omitted_sampled_issues": source_sampled - len(selected),
        "source_partial_or_unknown": source_gap,
        "display_partial": display_gap,
        "partial_or_unknown": source_gap or display_gap,
        "all_repositories_covered": not stopped_for_provider,
        "source_sample_is_not_complete_backlog_proof": True,
        "active_worker_ownership_verified": False,
        "required_ci_verified": False,
        "review_verified": False,
        "merge_authorized": False,
        "deploy_authorized": False,
        "actions_executed": False,
        "issues": selected,
    }


def as_markdown(result):
    rows = [
        "# zSentinel — portfolio GitHub issue desk", "",
        "**Human-only source observations; no claims, CI reruns, merges or deploys.**",
        "",
        "- Observed at: " + result["generated_at_utc"],
        "- Repositories read: " + str(result["read_count"]) + "/" +
        str(result["requested_count"]),
        "- Sampled across observed sources: " + str(result["total_sampled_issues"]),
        "- Selected display view: " + result["display_view"],
        "- Explicit P0 labels in sampled sources: " +
        str(result["source_priority_counts"]["label_p0"]),
        "- Explicit P1 labels in sampled sources: " +
        str(result["source_priority_counts"]["label_p1"]),
        "- Unverified title P0/P1 hints: " +
        str(result["source_priority_counts"]["unverified_title_hint"]),
        "- Shown P0-first: " + str(result["total_displayed_issues"]),
        "- Omitted due to display cap: " +
        str(result["omitted_due_to_display_limit"]),
        "- Not selected by current display view: " +
        str(result["filtered_out_by_view"]),
        "- Omitted sampled cards: " + str(result["omitted_sampled_issues"]),
        "- GitHub source partial or unknown: " +
        str(result["source_partial_or_unknown"]),
        "- Display filter/cap omitted source cards: " +
        str(result["display_partial"]),
        "- Combined report partial or unknown: " +
        str(result["partial_or_unknown"]),
        "- Repositories sampled sequentially; not one atomic GitHub snapshot.",
        "- No active ownership or exact-HEAD GitHub release authorization verified.",
        "", "## GitHub source coverage", "",
    ]
    for r in result["repositories_observed"]:
        rows.append(
            "- " + r["repository"] + " · " + r["state"] +
            " · observed: " + (r["observed_at_utc"] or "UNKNOWN") +
            " · sampled: " + str(r["sampled_count"] if
                                    r["sampled_count"] is not None else "UNKNOWN") +
            " · complete recent inventory: " + str(r["recent_window_complete"]) +
            " · source P0/P1: " + (
                str(r["source_priority_counts"]["label_p0"]) + "/" +
                str(r["source_priority_counts"]["label_p1"])
                if r["source_priority_counts"] is not None else "UNKNOWN"
            ) + " · next: " + r["human_source_followup_code"]
        )
        rows.append("  - Human follow-up: " + r["human_source_followup"] +
                    " (not executed)")
    rows += ["", "## Issue workorders · source P0/P1 first", ""]
    for issue in result["issues"]:
        rows += [
            "### [" + issue["repository"] + " #" + str(issue["number"]) +
            "](" + issue["url"] + ")",
            "- Observed label: " + issue["explicit_priority"] +
            " · unverified title hint: " + issue["title_hint_unverified"],
            "- **Problem:** " + issue["problem"],
            "- **Human action:** " + issue["human_next_action"] +
            " · code: " + issue["human_action_code"],
            "- **Status:** " + issue["status"],
            "- **Next existing-PR live desk:** `" +
            issue["single_issue_live_pr_review_command"] +
            "` (not executed)", "",
        ]
    if not result["issues"]:
        rows.append(
            "No issue cards selected; this is not evidence of an empty live backlog."
        )
    return "\n".join(rows) + "\n"


def as_html(result):
    esc = lambda value: html.escape(str(value), quote=True)
    rows = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none';\">",
        "<title>zSentinel | portfolio issue desk</title>",
        "<style>body{font:16px/1.5 system-ui,sans-serif;max-width:1000px;margin:auto;padding:1.4rem;color:#203047;background:#f7f9fc}section,header{background:white;border:1px solid #cbd5e1;border-radius:.5rem;padding:1rem;margin:1rem 0}a{color:#13549b}a:focus-visible{outline:3px solid #13549b}li{margin:.75rem 0;overflow-wrap:anywhere}.hold{border-left:5px solid #b56b22}code{overflow-wrap:anywhere}</style>",
        "</head><body><header><h1>zSentinel — portfolio issue workorders</h1>",
        "<p><strong>Advisory only.</strong> GitHub issues were sampled sequentially, not atomically. No worker claim, CI, independent review, merge or deployment was verified.</p>",
        "<p>Repositories read: " + esc(result["read_count"]) + "/" +
        esc(result["requested_count"]) + " · sampled: " +
        esc(result["total_sampled_issues"]) + " · displayed: " +
        esc(result["total_displayed_issues"]) + " · omitted: " +
        esc(result["omitted_sampled_issues"]) +
        " · GitHub source gap: " +
        esc(result["source_partial_or_unknown"]) +
        " · omitted sampled cards: " + esc(result["display_partial"]) +
        "</p><p>View " + esc(result["display_view"]) +
        " · source labels P0/P1: " +
        esc(result["source_priority_counts"]["label_p0"]) + "/" +
        esc(result["source_priority_counts"]["label_p1"]) +
        " · title-only hints: " +
        esc(result["source_priority_counts"]["unverified_title_hint"]) +
        " · filtered by view: " + esc(result["filtered_out_by_view"]) +
        " · hidden by display cap: " +
        esc(result["omitted_due_to_display_limit"]) + "</p></header>",
        "<section><h2>Original repository source coverage</h2><ul>",
    ]
    for r in result["repositories_observed"]:
        rows.append(
            "<li><strong>" + esc(r["repository"]) + "</strong> · " +
            esc(r["state"]) + " · observed " +
            esc(r["observed_at_utc"] or "UNKNOWN") + " · sampled " +
            esc(r["sampled_count"] if r["sampled_count"] is not None else "UNKNOWN") +
            " · complete recent window: " + esc(r["recent_window_complete"]) +
            " · sampled label P0/P1: " +
            (esc(r["source_priority_counts"]["label_p0"]) + "/" +
             esc(r["source_priority_counts"]["label_p1"])
             if r["source_priority_counts"] is not None else "UNKNOWN") +
            "<br><strong>Human source action:</strong> " +
            esc(r["human_source_followup"]) +
            " <small>(not executed)</small></li>"
        )
    rows.append("</ul></section><main><h2>P0-first human owner routes</h2>")
    groups = (
        ("label-p0", "Explicit P0 GitHub labels", (0,)),
        ("label-p1", "Explicit P1 GitHub labels", (1,)),
        ("title-hints", "Unverified title-only P0/P1 hints", (2, 3)),
        ("label-p2-p3", "Explicit P2/P3 GitHub labels", (4, 5)),
        ("unknown", "Unknown, incomplete or conflicting priorities", (6,)),
    )
    grouped = [
        (key, label, [
            issue for issue in result["issues"]
            if issue["priority_rank"] in ranks
        ]) for key, label, ranks in groups
    ]
    rows.append("<nav aria-label='Issue urgency groups'><p>Jump to: " +
                " · ".join(
                    "<a href='#" + key + "'>" + esc(label) +
                    " (" + str(len(items)) + ")</a>"
                    for key, label, items in grouped if items
                ) + "</p></nav>")
    for key, label, items in grouped:
        if not items:
            continue
        rows.append("<div id='" + key + "'><h3>" + esc(label) +
                    " (" + str(len(items)) + ")</h3>")
        for issue in items:
            rows += [
                "<section class='hold'><h4><a target='_blank' rel='noopener noreferrer' href='" +
                esc(issue["url"]) + "'>" + esc(issue["repository"]) + " #" +
                esc(issue["number"]) + "</a></h4>",
                "<p>Explicit label: " + esc(issue["explicit_priority"]) +
                " · unverified title hint: " + esc(issue["title_hint_unverified"]) + "</p>",
                "<p><strong>Problem:</strong> " + esc(issue["problem"]) + "<br>" +
                "<strong>Human action:</strong> " + esc(issue["human_next_action"]) +
                "<br><strong>Status:</strong> " + esc(issue["status"]) +
                "</p><p><strong>Inspect existing PRs:</strong> <code>" +
                esc(issue["single_issue_live_pr_review_command"]) +
                "</code> (not executed)</p></section>",
            ]
        rows.append("</div>")
    if not result["issues"]:
        rows.append(
            "<section>No issues observed in the selected bounded windows. "
            "Live backlog absence is NOT verified.</section>"
        )
    rows.append("</main></body></html>\n")
    return "\n".join(rows)


def as_csv(result):
    sink = io.StringIO(newline="")
    writer = csv.writer(sink, lineterminator="\n")
    fields = (
        "row_kind", "source_repository", "source_observed_utc",
        "source_state", "sampled_issue_count", "source_partial_unknown",
        "display_view", "source_label_p0", "source_label_p1",
        "source_followup_code", "source_followup_executed",
        "issue_url", "priority_label", "title_hint_unverified",
        "human_action_code", "human_next_action", "status",
        "single_issue_live_pr_review_command", "command_executed",
        "active_claim_verified", "required_ci_verified",
        "merge_authorized", "deploy_authorized",
    )
    writer.writerow(fields)

    def inert(value):
        if value is None:
            return ""
        s = str(value)
        pos = 0
        while pos < len(s) and (
            s[pos].isspace() or unicodedata.category(s[pos]) in
            ("Cf", "Zs", "Zl", "Zp")
        ):
            pos += 1
        return "'" + s if s[pos:].startswith(("=", "+", "-", "@")) else s

    known = {r["repository"]: r for r in result["repositories_observed"]}
    for r in result["repositories_observed"]:
        writer.writerow([inert(x) for x in (
            "source", r["repository"], r["observed_at_utc"],
            r["state"], r["sampled_count"],
            r["source_partial_or_unknown"], result["display_view"],
            r["source_priority_counts"]["label_p0"] if r["source_priority_counts"] else None,
            r["source_priority_counts"]["label_p1"] if r["source_priority_counts"] else None,
            r["human_source_followup_code"], False,
            "", "", "", "", "",
            "SOURCE COVERAGE IS NOT LIVE OWNER AUTHORITY",
            "", False, False, False, False, False,
        )])
    for issue in result["issues"]:
        r = known[issue["repository"]]
        writer.writerow([inert(x) for x in (
            "issue", issue["repository"], r["observed_at_utc"],
            r["state"], r["sampled_count"],
            r["source_partial_or_unknown"], result["display_view"],
            r["source_priority_counts"]["label_p0"],
            r["source_priority_counts"]["label_p1"],
            r["human_source_followup_code"], False, issue["url"],
            issue["explicit_priority"], issue["title_hint_unverified"],
            issue["human_action_code"], issue["human_next_action"],
            issue["status"], issue["single_issue_live_pr_review_command"],
            False, False, False, False, False,
        )])
    return sink.getvalue()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Bounded multi-repository GitHub P0-first owner desk, read-only."
    )
    parser.add_argument(
        "--repo", action="append", required=True,
        help="Canonical Zennay/repo; repeat for 2–6 repositories."
    )
    parser.add_argument("--view", choices=tuple(VIEWS), default="all",
                        help="display-only priority slice; all source reads unchanged")
    parser.add_argument("--format", choices=("html", "markdown", "csv", "json"),
                        default="html")
    parser.add_argument("--output", type=Path, required=True,
                        help="Create-only private output path; no stdout mode.")
    args = parser.parse_args(argv)
    try:
        report = observe_portfolio(
            args.repo, token=os.environ.get("GITHUB_TOKEN") or None,
            view=args.view,
        )
        source = (
            as_html(report) if args.format == "html" else
            as_markdown(report) if args.format == "markdown" else
            as_csv(report) if args.format == "csv" else
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        )
        if not 1 <= len(source.encode("utf-8")) <= MAX_OUTPUT_BYTES:
            raise PolicyInputError(
                "portfolio owner handoff exceeds 128 KiB; reduce repositories"
            )
        _emit_output(source, args.output, args.format)
        return 0
    except (ValueError, OSError, UnicodeError, GitHubReadError, PolicyInputError) as exc:
        print("zSentinel portfolio issue desk: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

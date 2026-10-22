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

from zsentinel_live_issue_inbox import (
    REPO, MAX_PAGES, MAX_DISPLAY, observe_repo_inbox,
)
from zsentinel_pr_live_read import GitHubReadError
from zsentinel_recovery_policy import (
    MAX_OUTPUT_BYTES, PolicyInputError, _emit_output,
)

MAX_REPOS = 6
MAX_GLOBAL_CARDS = 120
ALL_RANKS = range(7)


def observe_portfolio(repositories, *, token=None):
    """Read each explicitly selected repository once; never infer an absent issue."""
    if not isinstance(repositories, list) or not 2 <= len(repositories) <= MAX_REPOS:
        raise ValueError("choose 2–6 canonical Zennay repositories")
    if (any(type(name) is not str or REPO.fullmatch(name) is None
            for name in repositories) or len(set(repositories)) != len(repositories)):
        raise ValueError("repositories must be distinct canonical Zennay/name values")
    recorded = []
    ranked = {}
    stopped_for_provider = False
    for name in repositories:
        if stopped_for_provider:
            recorded.append({
                "repository": name, "state": "NOT_READ_AFTER_SOURCE_ERROR",
                "observed_at_utc": None, "sampled_count": None,
                "recent_window_complete": False, "source_partial_or_unknown": True,
                "provider_issue_count": None, "get_attempts": 0,
                "source_error": "previous_repo_source_unavailable",
                "unknown_issue_absence": True,
            })
            continue
        source = observe_repo_inbox(
            name, token=token, display_all_sample=True,
        )
        error = source["source_error"]
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

    # P0/P1 first ACROSS repositories, round-robin for project fairness.
    # This cannot promote a title hint above an actual source label.
    selected = []
    for priority in ALL_RANKS:
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
    actual_gets = sum(r["get_attempts"] for r in recorded)
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
        "bounded_get_ceiling": 7 * len(repositories),
        "total_sampled_issues": source_sampled,
        "total_displayed_issues": len(selected),
        "omitted_sampled_issues": source_sampled - len(selected),
        "partial_or_unknown": (
            stopped_for_provider or any(
                item["source_partial_or_unknown"] for item in recorded
            ) or source_sampled > len(selected)
        ),
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
        "- Shown P0-first: " + str(result["total_displayed_issues"]),
        "- Omitted sampled cards: " + str(result["omitted_sampled_issues"]),
        "- Source partial or unknown: " + str(result["partial_or_unknown"]),
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
            " · complete recent inventory: " + str(r["recent_window_complete"])
        )
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
            "- **Status:** " + issue["status"], "",
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
        " · partial/unknown: " + esc(result["partial_or_unknown"]) + "</p></header>",
        "<section><h2>Original repository source coverage</h2><ul>",
    ]
    for r in result["repositories_observed"]:
        rows.append(
            "<li><strong>" + esc(r["repository"]) + "</strong> · " +
            esc(r["state"]) + " · observed " +
            esc(r["observed_at_utc"] or "UNKNOWN") + " · sampled " +
            esc(r["sampled_count"] if r["sampled_count"] is not None else "UNKNOWN") +
            " · complete recent window: " + esc(r["recent_window_complete"]) +
            "</li>"
        )
    rows.append("</ul></section><main><h2>P0-first human owner routes</h2>")
    for issue in result["issues"]:
        rows += [
            "<section class='hold'><h3><a target='_blank' rel='noopener noreferrer' href='" +
            esc(issue["url"]) + "'>" + esc(issue["repository"]) + " #" +
            esc(issue["number"]) + "</a></h3>",
            "<p>Explicit label: " + esc(issue["explicit_priority"]) +
            " · unverified title hint: " + esc(issue["title_hint_unverified"]) + "</p>",
            "<p><strong>Problem:</strong> " + esc(issue["problem"]) + "<br>" +
            "<strong>Human action:</strong> " + esc(issue["human_next_action"]) +
            "<br><strong>Status:</strong> " + esc(issue["status"]) + "</p></section>",
        ]
    if not result["issues"]:
        rows.append("<section>No issues observed in the selected bounded windows. Live backlog absence is NOT verified.</section>")
    rows.append("</main></body></html>\n")
    return "\n".join(rows)


def as_csv(result):
    sink = io.StringIO(newline="")
    writer = csv.writer(sink, lineterminator="\n")
    fields = (
        "row_kind", "source_repository", "source_observed_utc",
        "source_state", "sampled_issue_count", "source_partial_unknown",
        "issue_url", "priority_label", "title_hint_unverified",
        "human_action_code", "human_next_action", "status",
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
            r["source_partial_or_unknown"], "", "", "", "", "",
            "SOURCE COVERAGE IS NOT LIVE OWNER AUTHORITY",
            False, False, False, False,
        )])
    for issue in result["issues"]:
        r = known[issue["repository"]]
        writer.writerow([inert(x) for x in (
            "issue", issue["repository"], r["observed_at_utc"],
            r["state"], r["sampled_count"],
            r["source_partial_or_unknown"], issue["url"],
            issue["explicit_priority"], issue["title_hint_unverified"],
            issue["human_action_code"], issue["human_next_action"],
            issue["status"], False, False, False, False,
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
    parser.add_argument("--format", choices=("html", "markdown", "csv", "json"),
                        default="html")
    parser.add_argument("--output", type=Path, required=True,
                        help="Create-only private output path; no stdout mode.")
    args = parser.parse_args(argv)
    try:
        report = observe_portfolio(
            args.repo, token=os.environ.get("GITHUB_TOKEN") or None,
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

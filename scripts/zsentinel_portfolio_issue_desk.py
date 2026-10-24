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
from zsentinel_portfolio_history import (
    load_previous_snapshot, compare_snapshots,
    as_markdown as history_markdown, as_html as history_html,
    append_csv as history_csv,
)

MAX_REPOS = 6
MAX_GLOBAL_CARDS = 120
ALL_RANKS = range(7)


def _repository_local_unavailable(error):
    """Scope canonical not-found and unconfirmed repository 403s to one repo.

    Confirmed authentication/global cooldown, network and malformed-response
    ambiguity remain provider-wide UNKNOWN and stop further portfolio reads.
    """
    return isinstance(error, str) and (
        "inaccessible or not found" in error
    )
VIEWS = {
    "all": frozenset(ALL_RANKS),
    "explicit-critical": frozenset((0, 1)),
    "critical-and-title-hints": frozenset((0, 1, 2, 3)),
}


def _assignee_display(issue):
    """Render GitHub issue assignment without implying an active worker claim."""
    if issue.get("github_assignees_source_complete") is not True:
        return "UNKNOWN"
    names = issue.get("github_assignees")
    if not isinstance(names, list):
        return "UNKNOWN"
    return ", ".join(names) if names else "none assigned in issue metadata"


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
    repository_local_gaps = 0
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
                "source_error_scope": "provider_or_unknown",
                "unknown_issue_absence": True,
                "source_priority_counts": None,
            })
            continue
        try:
            source = observe_repo_inbox(
                name, token=token, display_all_sample=True,
            )
        except GitHubReadError as exc:
            # A 404 or non-global 403 must not hide the next independent
            # project. Confirmed auth/global cooldown and network uncertainty
            # still stop provider reads; no provider error body is exposed.
            local_error = _repository_local_unavailable(str(exc))
            recorded.append({
                "repository": name, "state": "SOURCE_UNAVAILABLE",
                "observed_at_utc": None, "sampled_count": None,
                "recent_window_complete": False,
                "source_partial_or_unknown": True,
                "provider_issue_count": None,
                "get_attempts": None,
                "source_error": "github_repository_unavailable" if local_error
                else "github_source_read_failed",
                "source_error_scope": "repository_only" if local_error
                else "provider_or_unknown",
                "unknown_issue_absence": True,
                "source_priority_counts": None,
            })
            if local_error:
                repository_local_gaps += 1
            else:
                stopped_for_provider = True
            continue
        error = source["source_error"]
        # A real source error must never coexist with a misleading complete
        # sample flag, even if the nested reader did not propagate its error
        # into the older completeness boolean.
        source_partial = bool(error) or source["source_partial_or_unknown"]
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
            "state": "INCOMPLETE" if source_partial else "SAMPLED",
            "observed_at_utc": source["observed_at_utc"],
            "sampled_count": source["total_issues_seen"],
            "recent_window_complete": source["recent_issue_window_complete"],
            "source_partial_or_unknown": source_partial,
            "provider_issue_count": source["provider_total_issue_count_at_observation"],
            "get_attempts": source["get_attempts"],
            "source_error": "github_repository_unavailable"
            if _repository_local_unavailable(error) else
            "github_source_read_failed" if error else None,
            "source_error_scope": (
                "repository_only" if _repository_local_unavailable(error) else
                "provider_or_unknown" if error else None
            ),
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
            # A repository-specific 404 impacts only this one project.
            # Provider-wide or ambiguous failures stop further reads.
            if _repository_local_unavailable(error):
                repository_local_gaps += 1
            else:
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
                    "github_assignees": issue["github_assignees"],
                    "github_assignees_source_complete": issue["github_assignees_source_complete"],
                    "assignee_window_identity_drift": issue["assignee_window_identity_drift"],
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
        "repository_local_source_gaps": repository_local_gaps,
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
        "all_repositories_covered": all(
            item["observed_at_utc"] is not None for item in recorded
        ),
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
        "- Repositories not observed because of a local source failure: " +
        str(result["repository_local_source_gaps"]),
        "- All requested repositories observed: " +
        str(result["all_repositories_covered"]),
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
            ) + " · source failure scope: " +
            (r["source_error_scope"] or "none") +
            " · next: " + r["human_source_followup_code"]
        )
        rows.append("  - Human follow-up: " + r["human_source_followup"] +
                    " (not executed)")
    rows += ["", "## Issue workorders · source P0/P1 first", ""]
    for issue in result["issues"]:
        assignees = _assignee_display(issue)
        rows += [
            "### [" + issue["repository"] + " #" + str(issue["number"]) +
            "](" + issue["url"] + ")",
            "- Observed label: " + issue["explicit_priority"] +
            " · unverified title hint: " + issue["title_hint_unverified"],
            "- GitHub issue assignees (not active claims): " + assignees +
            " · assignee source complete: " + str(issue["github_assignees_source_complete"]) +
            " · assignee drift between source windows: " +
            str(issue["assignee_window_identity_drift"]),
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
    output = "\n".join(rows) + "\n"
    if "comparison" in result:
        output += history_markdown(result["comparison"])
    return output


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
        esc(result["requested_count"]) +
        " · local repo source gaps: " +
        esc(result["repository_local_source_gaps"]) + " · sampled: " +
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
            " · source failure scope: " +
            esc(r["source_error_scope"] or "none") +
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
            assignees = _assignee_display(issue)
            rows += [
                "<section class='hold'><h4><a target='_blank' rel='noopener noreferrer' href='" +
                esc(issue["url"]) + "'>" + esc(issue["repository"]) + " #" +
                esc(issue["number"]) + "</a></h4>",
                "<p>Explicit label: " + esc(issue["explicit_priority"]) +
                " · unverified title hint: " + esc(issue["title_hint_unverified"]) + "</p>",
                "<p>GitHub issue assignees (not active claims): <code>" +
                esc(assignees) + "</code> · source complete: " +
                esc(issue["github_assignees_source_complete"]) +
                " · assignee drift: " + esc(issue["assignee_window_identity_drift"]) + "</p>",
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
    if "comparison" in result:
        rows.append(history_html(result["comparison"]))
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
        "source_error_scope", "issue_url", "github_assignees",
        "github_assignees_source_complete", "assignee_window_identity_drift",
        "priority_label", "title_hint_unverified", "human_action_code", "human_next_action", "status",
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
            r["source_error_scope"], "", "", "", "", "", "", "", "",
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
            r["human_source_followup_code"], False,
            r["source_error_scope"], issue["url"],
            _assignee_display(issue), issue["github_assignees_source_complete"],
            issue["assignee_window_identity_drift"],
            issue["explicit_priority"], issue["title_hint_unverified"],
            issue["human_action_code"], issue["human_next_action"],
            issue["status"], issue["single_issue_live_pr_review_command"],
            False, False, False, False, False,
        )])
    output = sink.getvalue()
    if "comparison" in result:
        output = history_csv(output, result["comparison"])
    return output


def _same_observation_snapshot(report):
    """Save raw bounded source cards only, not a nested previous comparison.

    The snapshot is reusable for a later invocation, with the same
    sampled-at timestamps as the rendered operator report.
    """
    sample = {key: value for key, value in report.items()
              if key != "comparison"}
    return json.dumps(sample, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def write_private_portfolio_outputs(report, *, format_name, output,
                                    snapshot_output=None):
    """Create report and optional raw snapshot from ONE source observation.

    Never overwrite any operator artifact. The two outputs have distinct
    paths, are individually create-only and private, and are size-bounded
    before any output is created.
    """
    if format_name not in ("html", "markdown", "csv", "json"):
        raise PolicyInputError("unsupported portfolio private output format")
    if not isinstance(output, Path):
        raise PolicyInputError("portfolio requires a private output path")
    if snapshot_output is not None:
        if not isinstance(snapshot_output, Path) or snapshot_output.suffix != ".json":
            raise PolicyInputError("paired source snapshot must be a .json file")
        if os.path.abspath(output) == os.path.abspath(snapshot_output):
            raise PolicyInputError("report and raw snapshot paths must differ")
    rendered = (
        as_html(report) if format_name == "html" else
        as_markdown(report) if format_name == "markdown" else
        as_csv(report) if format_name == "csv" else
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    snapshot = _same_observation_snapshot(report) if snapshot_output else None
    for name, content in (("portfolio report", rendered),
                          ("paired source snapshot", snapshot)):
        if content is not None and not 1 <= len(content.encode("utf-8")) <= MAX_OUTPUT_BYTES:
            raise PolicyInputError(name + " exceeds the 128 KiB private output bound")
    # Write the snapshot first, allowing a subsequent run to recover source
    # evidence if writing the formatted operator report fails. Both retain
    # the same observed source timestamp, even in a partial filesystem write.
    if snapshot_output is not None:
        _emit_output(snapshot, snapshot_output, "json")
    _emit_output(rendered, output, format_name)
    return True


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
    parser.add_argument("--snapshot-output", type=Path,
                        help="optional second create-only .json snapshot from same GitHub reads")
    parser.add_argument("--verify-missing-live", action="store_true",
                        help="opt-in bounded missing-issue GitHub detail reads, requires prior snapshot")
    parser.add_argument("--live-recheck-limit", type=int, default=3,
                        help="max 1..6 extra read-only GitHub detail GETs")
    parser.add_argument("--previous-snapshot", type=Path,
                        help="Prior private JSON desk output for conservative offline visible-card comparison")
    args = parser.parse_args(argv)
    if args.verify_missing_live and args.previous_snapshot is None:
        parser.error("--verify-missing-live requires --previous-snapshot")
    if not args.verify_missing_live and args.live_recheck_limit != 3:
        parser.error("--live-recheck-limit requires --verify-missing-live")
    if args.verify_missing_live and not 1 <= args.live_recheck_limit <= 6:
        parser.error("--live-recheck-limit must be 1..6")
    try:
        previous = (load_previous_snapshot(args.previous_snapshot)
                    if args.previous_snapshot is not None else None)
        if previous is not None and (previous["repositories_requested"] != args.repo
                                     or previous["display_view"] != args.view):
            raise PolicyInputError("previous snapshot requires same repositories and view")
        report = observe_portfolio(
            args.repo, token=os.environ.get("GITHUB_TOKEN") or None,
            view=args.view,
        )
        if previous is not None:
            report["comparison"] = compare_snapshots(previous, report)
            if args.verify_missing_live:
                from zsentinel_issue_recheck import recheck_missing_live
                report["comparison"] = recheck_missing_live(
                    report["comparison"],
                    token=os.environ.get("GITHUB_TOKEN") or None,
                    max_reads=args.live_recheck_limit,
                    source_read_admitted=not report["provider_stopped_early"],
                )
        write_private_portfolio_outputs(
            report, format_name=args.format, output=args.output,
            snapshot_output=args.snapshot_output,
        )
        return 0
    except (ValueError, OSError, UnicodeError, GitHubReadError, PolicyInputError) as exc:
        print("zSentinel portfolio issue desk: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

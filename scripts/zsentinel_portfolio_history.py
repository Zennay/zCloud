#!/usr/bin/env python3
"""Compare two bounded zSentinel portfolio issue snapshots without inventing closures.

This is an offline, non-authorizing operator aid. Disappearance from a sampled
GitHub issue window is never proof of resolution or completed remediation.
"""
from __future__ import annotations

import argparse
import csv
import html
import io
import json
import os
import sys
from pathlib import Path
import re
from datetime import datetime, timezone

from zsentinel_live_issue_inbox import ISSUE, REPO
from zsentinel_recovery_policy import (
    MAX_OUTPUT_BYTES, PolicyInputError, _read_input, _emit_output,
)

MAX_DELTA_CARDS = 100
MAX_OPERATOR_ISSUE_WORKORDERS = 20
CHANGE_FOLLOWUP = {
    "NEWLY_OBSERVED_NOT_PROVEN_NEW": (
        "inspect_existing_prs_and_current_worker_claims",
        "Inspect the existing issue and linked product PRs; confirm current owner and exact-head gates.",
    ),
    "OBSERVED_PRIORITY_CHANGE": (
        "verify_current_priority_labels_and_owner",
        "Check current GitHub priority labels and the existing product owner before reordering work.",
    ),
    "PERSISTENT_P0_P1_WITHOUT_GITHUB_ISSUE_UPDATE": (
        "inspect_current_owner_progress_and_pr_ci",
        "Check current worker activity, the open product PR and its CI before diagnosing a stall.",
    ),
    "NOT_IN_CURRENT_DISPLAY_NOT_PROVEN_RESOLVED": (
        "recheck_exact_issue_state_before_closure",
        "Check the original GitHub issue directly; a bounded sample cannot prove issue closure.",
    ),
}
UTC_TIMESTAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:[.][0-9]{1,6})?Z\Z")
VALID_STATES = frozenset((
    "SAMPLED", "INCOMPLETE", "SOURCE_UNAVAILABLE",
    "NOT_READ_AFTER_SOURCE_ERROR",
))


def _timestamp(raw):
    if type(raw) is not str or UTC_TIMESTAMP.fullmatch(raw) is None:
        raise PolicyInputError("snapshot time must be UTC ISO 8601")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PolicyInputError("invalid snapshot observation time") from exc
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise PolicyInputError("snapshot time is not UTC")
    return parsed


def _validate_snapshot(report):
    if not isinstance(report, dict) or type(report.get("schema_version")) is not int or report["schema_version"] != 1 or (
        report.get("policy") != "zsentinel-portfolio-github-issue-desk-v1"
    ):
        raise PolicyInputError("previous report is not a portfolio issue desk snapshot")
    repositories = report.get("repositories_requested")
    if (not isinstance(repositories, list) or not 2 <= len(repositories) <= 6
            or any(type(name) is not str or REPO.fullmatch(name) is None
                   for name in repositories)
            or len(set(repositories)) != len(repositories)):
        raise PolicyInputError("snapshot repository list is invalid")
    if report.get("display_view") not in (
        "all", "explicit-critical", "critical-and-title-hints",
    ):
        raise PolicyInputError("snapshot display view is unsupported")
    observed = report.get("repositories_observed")
    if (not isinstance(observed, list) or len(observed) != len(repositories)):
        raise PolicyInputError("snapshot coverage rows do not match repositories")
    state_by_repo = {}
    for name, row in zip(repositories, observed):
        if (not isinstance(row, dict) or row.get("repository") != name
                or row.get("state") not in VALID_STATES
                or type(row.get("source_partial_or_unknown")) is not bool):
            raise PolicyInputError("snapshot source coverage is invalid")
        if row["state"] in ("SAMPLED", "INCOMPLETE"):
            _timestamp(row.get("observed_at_utc"))
        elif row.get("observed_at_utc") is not None:
            raise PolicyInputError("unread repository cannot have observation time")
        state_by_repo[name] = row["state"]
    issues = report.get("issues")
    if not isinstance(issues, list) or len(issues) > 120:
        raise PolicyInputError("snapshot displayed issue bound is invalid")
    unique = {}
    for item in issues:
        if not isinstance(item, dict):
            raise PolicyInputError("snapshot issue row must be an object")
        repo, num, url, rank = (
            item.get("repository"), item.get("number"),
            item.get("url"), item.get("priority_rank"),
        )
        issue_updated = item.get("issue_updated_at_utc")
        if issue_updated is not None:
            _timestamp(issue_updated)
        if (type(repo) is not str or repo not in state_by_repo or state_by_repo[repo] not in
                ("SAMPLED", "INCOMPLETE") or type(num) is not int or num < 1
                or type(rank) is not int or rank not in range(7)
                or not isinstance(url, str)):
            raise PolicyInputError("snapshot issue provenance is invalid")
        match = ISSUE.fullmatch(url)
        if match is None or match[1] != repo or int(match[2]) != num:
            raise PolicyInputError("snapshot issue URL does not match source")
        if url in unique:
            raise PolicyInputError("duplicate issue in source snapshot")
        unique[url] = {"repository": repo, "number": num,
                       "url": url, "priority_rank": rank,
                       "issue_updated_at_utc": issue_updated}
    return _timestamp(report.get("generated_at_utc")), repositories, state_by_repo, unique


def load_previous_snapshot(path):
    """Accept only a bounded, stable, regular JSON input file."""
    raw = _read_input(str(path))
    try:
        previous = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs,
                              parse_constant=_reject_nonstandard)
    except (ValueError, UnicodeError) as exc:
        raise PolicyInputError("previous snapshot contains invalid UTF-8 JSON") from exc
    _validate_snapshot(previous)
    return previous


def _unique_pairs(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise PolicyInputError("duplicate snapshot JSON property")
        obj[key] = value
    return obj


def _reject_nonstandard(_value):
    raise PolicyInputError("nonstandard snapshot JSON number")


def compare_snapshots(previous, current):
    """Compare visible issue cards only, without asserting issue closure."""
    before_at, before_repos, before_state, before_items = _validate_snapshot(previous)
    after_at, after_repos, after_state, after_items = _validate_snapshot(current)
    if before_repos != after_repos or previous["display_view"] != current["display_view"]:
        raise PolicyInputError("comparison requires identical repo order and display view")
    if before_at >= after_at:
        raise PolicyInputError("previous snapshot must precede current observation")
    changes = []
    coverage = []
    for repo in after_repos:
        comparable = (
            before_state[repo] in ("SAMPLED", "INCOMPLETE")
            and after_state[repo] in ("SAMPLED", "INCOMPLETE")
        )
        before_readable = before_state[repo] in ("SAMPLED", "INCOMPLETE")
        after_readable = after_state[repo] in ("SAMPLED", "INCOMPLETE")
        if before_readable and not after_readable:
            transition = "GITHUB_SOURCE_LOST"
            action = "confirm_repo_access_and_provider_admission"
        elif not before_readable and after_readable:
            transition = "GITHUB_SOURCE_RESTORED_NOT_BACKLOG_CLEARED"
            action = "reinspect_original_issues_and_existing_prs"
        elif not before_readable and not after_readable:
            transition = "SOURCE_UNAVAILABLE_IN_BOTH_SNAPSHOTS"
            action = "escalate_unresolved_source_access_to_owner"
        elif before_state[repo] != after_state[repo]:
            transition = "SOURCE_COVERAGE_CHANGED"
            action = "inspect_incomplete_source_windows"
        else:
            transition = "SOURCE_READABLE_IN_BOTH_SNAPSHOTS"
            action = "review_priority_changed_issue_cards"
        coverage.append({
            "repository": repo, "previous_state": before_state[repo],
            "current_state": after_state[repo],
            "comparable_visible_cards": comparable,
            "source_transition": transition,
            "human_source_action_code": action,
            "action_executed": False,
            "resolution_status": "NOT_VERIFIABLE_FROM_SAMPLES",
        })
        if not comparable:
            continue
        before = {k: v for k, v in before_items.items() if v["repository"] == repo}
        after = {k: v for k, v in after_items.items() if v["repository"] == repo}
        for url, item in after.items():
            if url not in before:
                kind = "NEWLY_OBSERVED_NOT_PROVEN_NEW"
            elif before[url]["priority_rank"] != item["priority_rank"]:
                kind = "OBSERVED_PRIORITY_CHANGE"
            elif (item["priority_rank"] in (0, 1)
                  and before[url]["issue_updated_at_utc"] is not None
                  and before[url]["issue_updated_at_utc"] == item["issue_updated_at_utc"]
                  and (after_at - before_at).total_seconds() >= 6 * 3600):
                kind = "PERSISTENT_P0_P1_WITHOUT_GITHUB_ISSUE_UPDATE"
            else:
                continue
            action_code, action = CHANGE_FOLLOWUP[kind]
            changes.append({
                **item, "change": kind,
                "human_followup_code": action_code,
                "human_followup": action,
                "prior_priority_rank": before[url]["priority_rank"]
                if url in before else None,
                "current_priority_rank": item["priority_rank"],
                "closure_verified": False, "action_executed": False,
            })
        for url, item in before.items():
            if url not in after:
                kind = "NOT_IN_CURRENT_DISPLAY_NOT_PROVEN_RESOLVED"
                action_code, action = CHANGE_FOLLOWUP[kind]
                changes.append({
                    **item, "change": kind,
                    "human_followup_code": action_code,
                    "human_followup": action,
                    "prior_priority_rank": item["priority_rank"],
                    "current_priority_rank": None,
                    "closure_verified": False, "action_executed": False,
                })
    # Critical observed cards first; missing cards are review-only, never closed.
    changes.sort(key=lambda row: (
        row["change"] == "NOT_IN_CURRENT_DISPLAY_NOT_PROVEN_RESOLVED",
        row["current_priority_rank"] if row["current_priority_rank"] is not None
        else row["prior_priority_rank"],
        row["repository"], row["number"],
    ))
    counts = {name: sum(c["change"] == name for c in changes) for name in (
        "NEWLY_OBSERVED_NOT_PROVEN_NEW", "OBSERVED_PRIORITY_CHANGE",
        "PERSISTENT_P0_P1_WITHOUT_GITHUB_ISSUE_UPDATE",
        "NOT_IN_CURRENT_DISPLAY_NOT_PROVEN_RESOLVED",
    )}
    report = {
        "schema_version": 1,
        "policy": "zsentinel-portfolio-visible-issue-delta-v1",
        "previous_observed_at_utc": previous["generated_at_utc"],
        "current_observed_at_utc": current["generated_at_utc"],
        "previous_snapshot_operator_supplied": True,
        "repositories_compared": coverage,
        "cards_compared_are_bounded_display_samples": True,
        "issues_resolved_or_closed_verified": False,
        "ownership_claims_verified": False, "ci_or_review_verified": False,
        "actions_executed": False, "merge_or_deploy_authorized": False,
        "counts": counts,
        "source_transition_counts": {
            key: sum(c["source_transition"] == key for c in coverage)
            for key in ("GITHUB_SOURCE_LOST",
                        "GITHUB_SOURCE_RESTORED_NOT_BACKLOG_CLEARED",
                        "SOURCE_UNAVAILABLE_IN_BOTH_SNAPSHOTS",
                        "SOURCE_COVERAGE_CHANGED",
                        "SOURCE_READABLE_IN_BOTH_SNAPSHOTS")
        },
        "total_changes": len(changes),
        "omitted_change_cards": max(0, len(changes) - MAX_DELTA_CARDS),
        "changes": changes[:MAX_DELTA_CARDS],
    }
    report["operator_workorders"] = _operator_workorders(report)
    return report


def _operator_workorders(delta):
    """Make one P0-first human work queue from already observed source cards.

    Workorders do not fetch GitHub or authorize queue, worker or release work.
    Historic priorities are hints; no source sample is a full backlog.
    """
    work = []
    for row in delta["changes"]:
        rank = (row["current_priority_rank"]
                if row["current_priority_rank"] is not None
                else row["prior_priority_rank"])
        current = row["current_priority_rank"] is not None
        live = row.get("live_issue_recheck_state")
        # The reader's ranks 0/1 are source-verified P0/P1 labels,
        # 2/3 title-only hints, 4/5 P2/P3 labels and 6 UNKNOWN.
        # Never present title text or a historical sample as verified P0/P1.
        if not current:
            priority_source = "HISTORICAL_SAMPLE_ONLY"
        elif rank in (0, 1):
            priority_source = "CURRENT_EXPLICIT_GITHUB_P0_P1_LABEL"
        elif rank in (2, 3):
            priority_source = "CURRENT_UNVERIFIED_TITLE_HINT"
        elif rank in (4, 5):
            priority_source = "CURRENT_EXPLICIT_GITHUB_P2_P3_LABEL"
        else:
            priority_source = "CURRENT_PRIORITY_UNKNOWN"
        work.append({
            "kind": "issue",
            "repository": row["repository"],
            "issue_url": row["url"],
            "issue_number": row["number"],
            "change": row["change"],
            "priority_rank_hint": rank,
            "priority_source": priority_source,
            "critical_priority_verified_from_current_label": (
                current and rank in (0, 1)
            ),
            "live_github_issue_state": live or "NOT_LIVE_CHECKED",
            "live_github_observed_at_utc": row.get(
                "live_issue_recheck_observed_at_utc"),
            "human_next_action_code": row.get(
                "live_next_action_code", row["human_followup_code"]),
            "human_next_action": row.get(
                "live_next_action", row["human_followup"]),
            "disposition": "HUMAN_REVIEW_REQUIRED",
            "active_claim_verified": False,
            "fix_merged_main_verified": False,
            "deployment_verified": False,
            "action_executed": False,
        })
    # Current label-verified P0/P1 before historical P0/P1, followed
    # by unverified title hints, lower priorities, and source UNKNOWN.
    # This ordering never upgrades a historical rank into live severity.
    def priority_bucket(row):
        rank = row["priority_rank_hint"]
        historical = row["priority_source"] == "HISTORICAL_SAMPLE_ONLY"
        if rank in (0, 1):
            return (2 if historical else 0) + rank
        if rank in (2, 3):
            return (6 if historical else 4) + (rank - 2)
        if rank in (4, 5):
            return (10 if historical else 8) + (rank - 4)
        return 13 if historical else 12

    work.sort(key=lambda row: (
        priority_bucket(row), row["repository"], row["issue_number"],
    ))
    # Keep the advisory operator desk concise and bounded. The historical
    # delta still retains its independent 100-card window and omitted totals.
    # Reserve six extra slots for source gaps, never silently hide them under
    # a large critical-issue display.
    total_issue_candidates = len(work)
    work = work[:MAX_OPERATOR_ISSUE_WORKORDERS]
    for row in delta["repositories_compared"]:
        if row["source_transition"] == "SOURCE_READABLE_IN_BOTH_SNAPSHOTS":
            continue
        work.append({
            "kind": "source_coverage",
            "repository": row["repository"],
            "issue_url": None,
            "issue_number": None,
            "change": row["source_transition"],
            "priority_rank_hint": None,
            "priority_source": "SOURCE_COVERAGE_NOT_ISSUE_PRIORITY",
            "critical_priority_verified_from_current_label": False,
            "live_github_issue_state": "UNKNOWN",
            "live_github_observed_at_utc": None,
            "human_next_action_code": row["human_source_action_code"],
            "human_next_action": (
                "Recheck the source owner and access before interpreting "
                "missing sampled GitHub issues for this repository."
            ),
            "disposition": "HUMAN_REVIEW_REQUIRED",
            "active_claim_verified": False,
            "fix_merged_main_verified": False,
            "deployment_verified": False,
            "action_executed": False,
        })
    counts = {}
    for name in delta["repositories_compared"]:
        counts[name["repository"]] = {
            "issue_workorders": sum(
                row["repository"] == name["repository"]
                for row in work if row["kind"] == "issue"
            ),
            "source_coverage_workorders": sum(
                row["repository"] == name["repository"]
                for row in work if row["kind"] == "source_coverage"
            ),
        }
    return {
        "schema_version": 1,
        "policy": "zsentinel-portfolio-human-workorders-v1",
        "sampled_only": True,
        "not_complete_backlog_proof": True,
        "operator_attention_requires_live_owner_verification": True,
        "priority_is_only_source_sample_or_historical_hint": True,
        "release_authorized": False,
        "actions_executed": False,
        "count": len(work),
        "total_issue_candidates": total_issue_candidates,
        "omitted_issue_workorders": max(
            0, total_issue_candidates - MAX_OPERATOR_ISSUE_WORKORDERS
        ),
        "hidden_historical_changes_not_classified": delta["omitted_change_cards"],
        "by_repository": counts,
        "workorders": work,
    }


def _operator_markdown(delta):
    handoff = delta["operator_workorders"]
    rows = [
        "", "## Human operator workorders · source P0/P1 first", "",
        "Advisory only. No ownership, required CI, merge or production state "
        "is established from these sampled observations.",
        "",
        "- Human workorders: " + str(handoff["count"]),
        "- Extra issue workorders omitted from compact desk: " +
        str(handoff["omitted_issue_workorders"]),
        "- Unclassified hidden history changes: " +
        str(handoff["hidden_historical_changes_not_classified"]),
    ]
    for item in handoff["workorders"]:
        ref = ("[" + item["repository"] + " #" + str(item["issue_number"]) +
               "](" + item["issue_url"] + ")" if item["issue_url"] else
               item["repository"] + " · SOURCE COVERAGE")
        rows.append(
            "- " + ref + " · " + item["change"] +
            " · priority evidence " + item["priority_source"] +
            " · GitHub detail " + item["live_github_issue_state"] +
            " · **Human next:** " + item["human_next_action"] +
            " (" + item["human_next_action_code"] + ") · NOT EXECUTED"
        )
    if not handoff["workorders"]:
        rows.append(
            "No changed source cards selected; this is NOT proof of a clear backlog."
        )
    return "\n".join(rows) + "\n"


def _operator_html(delta):
    esc = lambda item: html.escape(str(item), quote=True)
    handoff = delta["operator_workorders"]
    rows = [
        "<section><h2>Human operator workorders · P0/P1 source first</h2>",
        "<p>Advisory only: verify active owner, required CI, merge and runtime "
        "independently. A missing or closed sampled issue is not shipped proof.</p>",
        "<p>Workorders: " + esc(handoff["count"]) +
        " · extra issue workorders not shown: " +
        esc(handoff["omitted_issue_workorders"]) + " · hidden changes not "
        "classified: " + esc(handoff["hidden_historical_changes_not_classified"]) +
        "</p><ol>",
    ]
    for item in handoff["workorders"]:
        label = esc(item["repository"]) + (
            " #" + esc(item["issue_number"]) if item["issue_url"] else
            " · source coverage"
        )
        link = (
            "<a target='_blank' rel='noopener noreferrer' href='" +
            esc(item["issue_url"]) + "'>" + label + "</a>"
            if item["issue_url"] else label
        )
        rows.append(
            "<li>" + link + " · " + esc(item["change"]) +
            " · " + esc(item["priority_source"]) +
            " · GitHub detail " + esc(item["live_github_issue_state"]) +
            " · <strong>Human next:</strong> " + esc(item["human_next_action"]) +
            " <code>" + esc(item["human_next_action_code"]) +
            "</code> · NOT EXECUTED</li>"
        )
    rows.append("</ol></section>")
    return "\n".join(rows)


def as_markdown(delta):
    rows = [
        "", "## Changes since previous bounded issue observation", "",
        "Previous snapshot is operator-supplied. Changed sample membership is "
        "**not** issue creation/closure, remediation, current ownership or release proof.",
        "",
        "- Previous: " + delta["previous_observed_at_utc"],
        "- Current: " + delta["current_observed_at_utc"],
    ]
    for name, count in delta["counts"].items():
        rows.append("- " + name + ": " + str(count))
    rows += ["- Hidden change cards: " + str(delta["omitted_change_cards"]), ""]
    if delta.get("live_issue_recheck"):
        live = delta["live_issue_recheck"]
        rows += [
            "- Explicit live issue state reads: " + str(live["attempted_gets"]) +
            "/" + str(live["maximum_reads"]),
            "- GitHub OPEN observed: " + str(live["open_at_recheck"]) +
            " · GitHub CLOSED observed: " + str(live["closed_at_recheck"]),
            "- Source error stopped further reads: " +
            str(live["provider_reads_stopped_after_error"]),
            "- A CLOSED state is not proof of a landed fix or deploy.",
        ]
    for coverage in delta["repositories_compared"]:
        rows.append("- " + coverage["repository"] + ": " + coverage["previous_state"] +
                    " → " + coverage["current_state"] + " · comparison possible: " +
                    str(coverage["comparable_visible_cards"]) +
                    " · " + coverage["source_transition"] +
                    " · human next: " + coverage["human_source_action_code"] +
                    " (not executed)")
    rows += ["", "### Visible-card changes", ""]
    for change in delta["changes"]:
        rows.append(
            "- [" + change["repository"] + " #" + str(change["number"]) +
            "](" + change["url"] + ") · " + change["change"] +
            " · prior rank " + str(change["prior_priority_rank"]) +
            " → current rank " + str(change["current_priority_rank"]) +
            " · **Human next:** " + change["human_followup"] +
            " · code: " + change["human_followup_code"] +
            (" · GitHub detail: " + change["live_issue_recheck_state"] +
             (" (" + change["live_issue_recheck_observed_at_utc"] + ")"
              if change.get("live_issue_recheck_observed_at_utc") else "") +
             " · human next: " + change["live_next_action"] +
             " (" + change["live_next_action_code"] + ")" +
             (" · GitHub closure reason: " + change["live_issue_state_reason"]
              if change.get("live_issue_state_reason") else "")
             if "live_issue_recheck_state" in change else "") +
            " · **NOT EXECUTED**"
        )
    if not delta["changes"]:
        rows.append("No comparable changed cards; this does not prove a clear backlog.")
    rows.append(_operator_markdown(delta))
    return "\n".join(rows) + "\n"


def as_html(delta):
    esc = lambda x: html.escape(str(x), quote=True)
    rows = [
        "<section aria-label='Source-bound issue changes'><h2>Changes since previous snapshot</h2>",
        "<p><strong>Not a closure or release report.</strong> The previous snapshot "
        "was supplied locally, and disappearing from a sampled window never proves resolution.</p>",
        "<p>Previous " + esc(delta["previous_observed_at_utc"]) +
        " · current " + esc(delta["current_observed_at_utc"]) + "</p>",
        "<p>" + " · ".join(esc(k) + ": " + esc(v)
                          for k, v in delta["counts"].items()) + "</p>",
        "<p>Hidden change cards: " + esc(delta["omitted_change_cards"]) + "</p><ul>",
    ]
    if delta.get("live_issue_recheck"):
        live = delta["live_issue_recheck"]
        rows.append("<p><strong>Opt-in GitHub detail recheck:</strong> " +
                    esc(live["attempted_gets"]) + "/" +
                    esc(live["maximum_reads"]) + " GETs · open " +
                    esc(live["open_at_recheck"]) + " · closed " +
                    esc(live["closed_at_recheck"]) +
                    " · stopped after provider error " +
                    esc(live["provider_reads_stopped_after_error"]) +
                    ". GitHub CLOSED does not prove a merged fix or deployment.</p>")
    for c in delta["repositories_compared"]:
        rows.append("<li>" + esc(c["repository"]) + ": " + esc(c["previous_state"]) +
                    " → " + esc(c["current_state"]) + " · comparable: " +
                    esc(c["comparable_visible_cards"]) + " · " +
                    esc(c["source_transition"]) + " · human next: " +
                    esc(c["human_source_action_code"]) +
                    " (not executed)</li>")
    rows.append("</ul><ol>")
    for c in delta["changes"]:
        rows.append(
            "<li><a target='_blank' rel='noopener noreferrer' href='" +
            esc(c["url"]) + "'>" + esc(c["repository"]) + " #" +
            esc(c["number"]) + "</a> · " + esc(c["change"]) +
            " · prior rank " + esc(c["prior_priority_rank"]) + " → current " +
            esc(c["current_priority_rank"]) + " · human next: " +
            esc(c["human_followup"]) +
            (" · GitHub detail: " + esc(c["live_issue_recheck_state"]) +
             (" · " + esc(c["live_issue_recheck_observed_at_utc"])
              if c.get("live_issue_recheck_observed_at_utc") else "") +
             " · human next: " + esc(c["live_next_action"]) +
             (" · GitHub closure reason: " + esc(c["live_issue_state_reason"])
              if c.get("live_issue_state_reason") else "")
             if "live_issue_recheck_state" in c else "") +
            " · NOT EXECUTED</li>"
        )
    rows.append("</ol></section>")
    rows.append(_operator_html(delta))
    return "\n".join(rows)


def append_csv(base, delta):
    """Append rows in the existing 24-field issue-desk CSV schema."""
    sink = io.StringIO(newline="")
    writer = csv.writer(sink, lineterminator="\n")
    for coverage in delta["repositories_compared"]:
        row = [""] * 24
        row[0] = "delta_source_transition"
        row[1] = coverage["repository"]
        row[3] = coverage["source_transition"]
        row[17] = coverage["human_source_action_code"] + " (NOT EXECUTED)"
        writer.writerow(row)
    for c in delta["changes"]:
        row = [""] * 24
        row[0] = "delta_" + c["change"].lower()
        row[1] = c["repository"]
        row[12] = c["url"]
        row[15] = c["human_followup_code"]
        row[16] = c["human_followup"]
        row[17] = ("GitHub issue detail " + c["live_issue_recheck_state"] +
                   " · human next: " + c["live_next_action_code"] +
                   (" · reason: " + c["live_issue_state_reason"]
                    if c.get("live_issue_state_reason") else "") +
                   " (NOT A FIX OR MERGE)" if "live_issue_recheck_state" in c
                   else "SAMPLED CHANGE; NO ISSUE CLOSURE, CLAIM OR MERGE VERIFIED")
        writer.writerow(row)
    for workorder in delta["operator_workorders"]["workorders"]:
        row = [""] * 24
        row[0] = "operator_" + workorder["kind"]
        row[1] = workorder["repository"]
        row[12] = workorder["issue_url"] or ""
        row[15] = workorder["human_next_action_code"]
        row[16] = workorder["human_next_action"]
        row[17] = (
            workorder["disposition"] + " · " +
            workorder["priority_source"] + " · GitHub " +
            workorder["live_github_issue_state"] + " · NOT EXECUTED"
        )
        writer.writerow(row)
    return base + sink.getvalue()


def as_delta_csv(delta):
    """Small human-only offline delta export, not a live issue inventory."""
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow((
        "row_kind", "repository", "issue_url", "change_type",
        "prior_priority_rank", "current_priority_rank",
        "source_transition", "human_source_action_code",
        "human_issue_action_code", "human_issue_followup",
        "issue_closure_verified", "actions_executed",
        "live_github_issue_detail", "live_observed_at_utc",
        "live_human_action_code", "live_github_state_reason",
    ))
    for row in delta["repositories_compared"]:
        writer.writerow((
            "source", row["repository"], "", "", "", "",
            row["source_transition"], row["human_source_action_code"],
            "", "", False, False, "", "", "", "",
        ))
    for row in delta["changes"]:
        writer.writerow((
            "issue", row["repository"], row["url"], row["change"],
            row["prior_priority_rank"], row["current_priority_rank"],
            "", "", row["human_followup_code"], row["human_followup"],
            False, False,
            row.get("live_issue_recheck_state", ""),
            row.get("live_issue_recheck_observed_at_utc") or "",
            row.get("live_next_action_code", ""),
            row.get("live_issue_state_reason") or "",
        ))
    for workorder in delta["operator_workorders"]["workorders"]:
        writer.writerow((
            "operator_" + workorder["kind"],
            workorder["repository"], workorder["issue_url"] or "",
            workorder["change"], "", workorder["priority_rank_hint"],
            workorder["priority_source"], "", workorder["human_next_action_code"],
            workorder["human_next_action"], False, False,
            workorder["live_github_issue_state"],
            workorder["live_github_observed_at_utc"] or "",
            workorder["human_next_action_code"], "",
        ))
    return stream.getvalue()


def as_full_html(delta):
    """Standalone offline HTML without script, storage, remote loads or writes."""
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<meta http-equiv='Content-Security-Policy' "
        "content=\"default-src 'none'; style-src 'unsafe-inline'; "
        "base-uri 'none'; form-action 'none';\">"
        "<title>zSentinel · Offline issue changes</title>"
        "<style>body{font:16px/1.5 system-ui,sans-serif;max-width:950px;"
        "margin:auto;padding:2rem;color:#203047;background:#f7f9fc}"
        "section{background:white;padding:1.25rem;border:1px solid #cbd5e1;"
        "border-radius:.5rem}a{color:#13549b}li{margin:1rem 0;"
        "overflow-wrap:anywhere}a:focus-visible{outline:3px solid #13549b}"
        "</style></head><body><h1>zSentinel · Offline issue changes</h1>"
        ("<p>GitHub issue-detail reads were explicitly requested; the "
         "observed GitHub issue state is not fix, claim, merge or deploy proof.</p>"
         if delta.get("live_issue_recheck") else
         "<p>Both snapshots were supplied by an operator; no new GitHub read "
         "or issue closure check occurred.</p>") + as_html(delta) +
        "</body></html>\n"
    )


def main(argv=None):
    """Compare two stored private JSON files when GitHub is unavailable."""
    parser = argparse.ArgumentParser(
        description="Offline, human-only two-snapshot zSentinel issue delta"
    )
    parser.add_argument("--previous", type=Path, required=True,
                        help="earlier private raw source snapshot JSON")
    parser.add_argument("--current", type=Path, required=True,
                        help="later private raw source snapshot JSON")
    parser.add_argument("--format", choices=("json", "markdown", "html", "csv"),
                        default="html")
    parser.add_argument("--output", type=Path, required=True,
                        help="new private 0600 output, never overwritten")
    parser.add_argument("--verify-missing-live", action="store_true",
                        help="opt-in at most six GitHub issue-only GETs for vanished cards")
    parser.add_argument("--live-recheck-limit", type=int, default=3,
                        help="max live GETs on opt-in; 1..6, default 3")
    args = parser.parse_args(argv)
    if not args.verify_missing_live and args.live_recheck_limit != 3:
        parser.error("--live-recheck-limit requires --verify-missing-live")
    if args.verify_missing_live and not 1 <= args.live_recheck_limit <= 6:
        parser.error("--live-recheck-limit must be 1..6")
    try:
        previous = load_previous_snapshot(args.previous)
        current = load_previous_snapshot(args.current)
        delta = compare_snapshots(previous, current)
        if args.verify_missing_live:
            from zsentinel_issue_recheck import recheck_missing_live
            delta = recheck_missing_live(
                delta, token=os.environ.get("GITHUB_TOKEN") or None,
                max_reads=args.live_recheck_limit,
                source_read_admitted=(current.get("provider_stopped_early") is False),
            )
        output = (
            as_full_html(delta) if args.format == "html" else
            as_markdown(delta) if args.format == "markdown" else
            as_delta_csv(delta) if args.format == "csv" else
            json.dumps(delta, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        )
        if not 1 <= len(output.encode("utf-8")) <= MAX_OUTPUT_BYTES:
            raise PolicyInputError("offline issue delta exceeds 128 KiB")
        _emit_output(output, args.output, args.format)
        return 0
    except (ValueError, OSError, UnicodeError, PolicyInputError) as exc:
        print("zSentinel offline issue history: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

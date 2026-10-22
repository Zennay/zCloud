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
import sys
from pathlib import Path
import re
from datetime import datetime, timezone

from zsentinel_live_issue_inbox import ISSUE, REPO
from zsentinel_recovery_policy import (
    MAX_OUTPUT_BYTES, PolicyInputError, _read_input, _emit_output,
)

MAX_DELTA_CARDS = 100
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
            changes.append({
                **item, "change": kind,
                "prior_priority_rank": before[url]["priority_rank"]
                if url in before else None,
                "current_priority_rank": item["priority_rank"],
                "closure_verified": False, "action_executed": False,
            })
        for url, item in before.items():
            if url not in after:
                changes.append({
                    **item, "change": "NOT_IN_CURRENT_DISPLAY_NOT_PROVEN_RESOLVED",
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
    return {
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
            " · **HUMAN FOLLOW-UP ONLY**"
        )
    if not delta["changes"]:
        rows.append("No comparable changed cards; this does not prove a clear backlog.")
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
            esc(c["current_priority_rank"]) + " · HUMAN FOLLOW-UP ONLY</li>"
        )
    rows.append("</ol></section>")
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
        row[17] = "SAMPLED CHANGE; NO ISSUE CLOSURE, CLAIM OR MERGE VERIFIED"
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
        "issue_closure_verified", "actions_executed",
    ))
    for row in delta["repositories_compared"]:
        writer.writerow((
            "source", row["repository"], "", "", "", "",
            row["source_transition"], row["human_source_action_code"],
            False, False,
        ))
    for row in delta["changes"]:
        writer.writerow((
            "issue", row["repository"], row["url"], row["change"],
            row["prior_priority_rank"], row["current_priority_rank"],
            "", "", False, False,
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
        "<p>Both snapshots were supplied by an operator; no new GitHub read "
        "or issue closure check occurred.</p>" + as_html(delta) +
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
    args = parser.parse_args(argv)
    try:
        previous = load_previous_snapshot(args.previous)
        current = load_previous_snapshot(args.current)
        delta = compare_snapshots(previous, current)
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

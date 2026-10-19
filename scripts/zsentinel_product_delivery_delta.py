#!/usr/bin/env python3
"""Compare two saved zSentinel delivery boards without inferring completion or owner release."""
from __future__ import annotations
import argparse
from datetime import timedelta
from html import escape
import json
from pathlib import Path
import sys

from zsentinel_product_delivery_board import read, when, write_create_only, ISSUE, PROJECT, URL


def keyed_cards(source):
    cards = source.get("cards")
    if type(cards) is not list or len(cards) > 60:
        raise ValueError("invalid bounded board card list")
    result = {}
    for card in cards:
        if type(card) is not dict or card.get("claim_authorized") is not False:
            raise ValueError("board card unexpectedly grants work authority")
        issue = card.get("issue")
        if type(issue) is not str or not ISSUE.fullmatch(issue) or issue in result:
            raise ValueError("ambiguous issue identity in board")
        project = card.get("project")
        if type(project) is not str or not PROJECT.fullmatch(project):
            raise ValueError("invalid board issue project")
        priority = card.get("priority")
        if priority not in ("P0", "P1", "P2", "Unspecified"):
            raise ValueError("invalid issue priority")
        references = card.get("open_pr_candidates")
        if type(references) is not list or len(references) > 3:
            raise ValueError("unbounded open PR candidates")
        if any(type(ref) is not str or not URL.fullmatch(ref) or "/pull/" not in ref
               for ref in references):
            raise ValueError("untrusted PR candidate URL")
        result[issue] = card
    return result


def project_rows(source):
    rows = source.get("project_operations")
    if type(rows) is not list or len(rows) > 60:
        raise ValueError("invalid project operations inventory")
    items = {}
    for row in rows:
        if type(row) is not dict or row.get("action_authorized") is not False:
            raise ValueError("invalid operator project entry")
        name = row.get("project")
        if type(name) is not str or not PROJECT.fullmatch(name) or name in items:
            raise ValueError("ambiguous project entry")
        items[name] = row
    return items


def metric(row, name):
    source = row.get("queue_observation")
    if source is None:
        return None
    if type(source) is not dict:
        raise ValueError("invalid retry observation")
    value = source.get(name)
    if type(value) is not int or value < 0 or value > 100000000:
        raise ValueError("invalid retry metric")
    return value


def pr_rows(source):
    rows = source.get("pr_delivery_observations", [])
    if type(rows) is not list or len(rows) > 5:
        raise ValueError("invalid bounded PR gate evidence")
    result = {}
    for row in rows:
        if type(row) is not dict or row.get("merge_authorized") is not False:
            raise ValueError("PR evidence attempted release authority")
        url, sha = row.get("url"), row.get("head_sha")
        if (type(url) is not str or not URL.fullmatch(url)
                or "/pull/" not in url
                or type(sha) is not str or len(sha) != 40
                or any(char not in "0123456789abcdef" for char in sha)
                or url in result):
            raise ValueError("ambiguous PR source identity")
        if type(row.get("observed_check_success")) is not bool:
            raise ValueError("invalid CI observation")
        approvals = row.get("observed_approvals")
        if type(approvals) is not int or not 0 <= approvals <= 100:
            raise ValueError("invalid approval evidence")
        result[url] = row
    return result


def compare(previous, current):
    if (previous.get("authorizes_claim") is not False or
        current.get("authorizes_claim") is not False or
        previous.get("authorizes_merge") is not False or
        current.get("authorizes_merge") is not False or
        previous.get("authorizes_deploy") is not False or
        current.get("authorizes_deploy") is not False or
        previous.get("automatic_dispatch_enabled") is not False or
        current.get("automatic_dispatch_enabled") is not False):
        raise ValueError("handoff attempted to assert operational authority")
    earlier, later = when(previous.get("observed_at_utc")), when(current.get("observed_at_utc"))
    if not earlier < later or later - earlier > timedelta(days=7):
        raise ValueError("board snapshots must increase within seven days")
    prev_cov, cur_cov = previous.get("coverage"), current.get("coverage")
    if type(prev_cov) is not dict or type(cur_cov) is not dict:
        raise ValueError("missing board coverage")
    left_scope, right_scope = prev_cov.get("projects"), cur_cov.get("projects")
    if type(left_scope) is not dict or type(right_scope) is not dict or set(left_scope) != set(right_scope):
        raise ValueError("project scope drift: compare only like-for-like snapshots")
    before, after = keyed_cards(previous), keyed_cards(current)
    before_projects, after_projects = project_rows(previous), project_rows(current)
    if set(before_projects) != set(after_projects):
        raise ValueError("project operations scope drift")
    surfaced, absent, changed = [], [], []
    for key in sorted(set(after) - set(before)):
        row = after[key]
        surfaced.append({"issue": key, "project": row.get("project"),
                         "priority": row["priority"],
                         "interpretation": "newly_observed_in_bounded_sample_not_a_new_issue"})
    for key in sorted(set(before) - set(after)):
        row = before[key]
        absent.append({"issue": key, "project": row.get("project"),
                       "interpretation": "not_seen_in_current_sample_not_resolved"})
    for key in sorted(set(before) & set(after)):
        left, right = before[key], after[key]
        if left["priority"] != right["priority"] or set(left["open_pr_candidates"]) != set(right["open_pr_candidates"]):
            changed.append({
                "issue": key, "project": right.get("project"),
                "priority_before": left["priority"], "priority_after": right["priority"],
                "candidate_prs_before": left["open_pr_candidates"],
                "candidate_prs_after": right["open_pr_candidates"],
                "interpretation": "issue_or_pr_reference_changed_not_integrated_or_reviewed",
            })
    previous_prs, current_prs = pr_rows(previous), pr_rows(current)
    pr_movements = []
    for link in sorted(set(previous_prs) | set(current_prs)):
        left, right = previous_prs.get(link), current_prs.get(link)
        if left is None or right is None:
            pr_movements.append({
                "url": link,
                "kind": "pr_not_sampled_in_one_snapshot_not_open_or_closed_proof",
                "head_changed": None, "observed_check_changed": None,
                "approval_count_changed": None,
            })
        elif (left["head_sha"] != right["head_sha"] or
              left["observed_check_success"] != right["observed_check_success"] or
              left["observed_approvals"] != right["observed_approvals"]):
            pr_movements.append({
                "url": link,
                "kind": "exact_head_or_check_or_review_observation_changed",
                "head_changed": left["head_sha"] != right["head_sha"],
                "observed_check_changed": (
                    left["observed_check_success"] != right["observed_check_success"]),
                "approval_count_changed": (
                    right["observed_approvals"] - left["observed_approvals"]),
            })
    project_deltas = []
    for project in sorted(before_projects):
        left, right = before_projects[project], after_projects[project]
        diffs = {}
        for metric_name in ("active_items", "high_claim_attempts", "expired_active_leases"):
            a, b = metric(left, metric_name), metric(right, metric_name)
            diffs[metric_name] = (b - a if a is not None and b is not None else None)
        project_deltas.append({
            "project": project, "queue_deltas": diffs,
            "claim_status": "not_authoritative",
            "owner_release_inferred": False,
        })
    return {
        "schema": "zsentinel-product-delivery-delta-v1",
        "previous_observed_at_utc": earlier.isoformat(),
        "current_observed_at_utc": later.isoformat(),
        "previous_coverage_partial": (prev_cov.get("inbox_truncated") is True or
                                      prev_cov.get("github_denial") is True),
        "current_coverage_partial": (cur_cov.get("inbox_truncated") is True or
                                     cur_cov.get("github_denial") is True),
        "newly_observed_in_sample": surfaced,
        "not_seen_in_current_sample": absent,
        "changed_issue_or_pr_references": changed,
        "project_queue_deltas": project_deltas,
        "pr_gate_observation_changes": pr_movements,
        "prs_integrated_inferred": False,
        "issues_resolved_inferred": False,
        "claims_released_inferred": False,
        "workflow_restarted": False,
        "authorizes_claim": False,
        "authorizes_merge": False,
        "authorizes_deploy": False,
        "operator_note": (
            "Two bounded snapshots are observations, not a complete GitHub or queue "
            "inventory. Missing issue means not sampled, never closed. A new PR "
            "reference is not a merged PR or reviewer approval. Claim attempts "
            "are not failure counts. Recheck live leases and release gates."
        ),
    }


def md(data):
    out = ["# zSentinel · Product delivery change handoff", "",
           "**No claim, merge, deploy or recovery authorization.**", "",
           "## Newly observed in bounded sample"]
    for row in data["newly_observed_in_sample"]:
        out.append("- " + row["priority"] + " · " + row["issue"])
    out.extend(["", "## No longer visible (NOT resolved)"])
    for row in data["not_seen_in_current_sample"]:
        out.append("- " + row["issue"] + " · absent from sample only")
    out.extend(["", "## Issue / open-PR references changed"])
    for row in data["changed_issue_or_pr_references"]:
        out.append("- " + row["issue"] + " · " + row["priority_before"] +
                   " → " + row["priority_after"] +
                   " · PR candidates " + str(len(row["candidate_prs_before"])) +
                   " → " + str(len(row["candidate_prs_after"])))
    out.extend(["", "## Exact-head PR/approval changes (NOT merge proof)"])
    for item in data["pr_gate_observation_changes"]:
        out.append("- " + item["url"] + " · " + item["kind"])
    out.extend(["", "## Project queue changes",
                "| Project | Active Δ | High claim cycles Δ | Expired lease Δ |",
                "|---|---:|---:|---:|"])
    for row in data["project_queue_deltas"]:
        v = row["queue_deltas"]
        out.append("| " + row["project"] + " | " +
                   " | ".join("unknown" if v[key] is None else str(v[key])
                       for key in ("active_items", "high_claim_attempts", "expired_active_leases")) +
                   " |")
    return "\n".join(out + ["", data["operator_note"], ""])


def html(data):
    def listing(items, message):
        return ("".join("<li>" + escape(row["issue"]) + "</li>" for row in items)
                or "<li>" + escape(message) + "</li>")
    pr_changes = "".join(
        "<li>" + escape(row["url"]) + " · " + escape(row["kind"]) +
        "</li>" for row in data["pr_gate_observation_changes"]
    ) or "<li>No observed change</li>"
    deltas = "".join(
        "<tr><td>" + escape(row["project"]) + "</td>" +
        "".join("<td>" + escape("unknown" if row["queue_deltas"][key] is None else
                                 str(row["queue_deltas"][key])) + "</td>"
                for key in ("active_items", "high_claim_attempts", "expired_active_leases")) +
        "</tr>" for row in data["project_queue_deltas"])
    css = """body{font:16px/1.5 system-ui,sans-serif;background:#121a25;color:#edf4ff;
max-width:1000px;margin:auto;padding:clamp(12px,4vw,32px)}
section{border:1px solid #3d5067;border-radius:12px;background:#1d2a3a;padding:14px;margin:15px 0}
h1{font-size:clamp(1.5rem,4vw,2.3rem)}h2{font-size:1.14rem}
table{width:100%;border-collapse:collapse}th,td{padding:8px;text-align:left;border-bottom:1px solid #44556a}
.notice{border-left:4px solid #e4ba70;padding:14px;background:#352d20}.scroll{overflow-x:auto}"""
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>zSentinel Product Change Handoff</title><style>' + css +
            '</style></head><body><h1>zSentinel · Product change handoff</h1>'
            '<p class="notice">' + escape(data["operator_note"]) + '</p>'
            '<section><h2>Newly observed (not necessarily new)</h2><ul>' +
            listing(data["newly_observed_in_sample"], "None in bounded sample") +
            '</ul></section><section><h2>Not currently sampled (NOT resolved)</h2><ul>' +
            listing(data["not_seen_in_current_sample"], "None in bounded sample") +
            '</ul></section><section><h2>PR / issue references changed</h2><ul>' +
            listing(data["changed_issue_or_pr_references"], "None observed") +
            '</ul></section><section><h2>PR exact-head/review observation movement</h2><ul>' +
            pr_changes +
            '</ul></section><section><h2>Queue movement</h2><div class="scroll">'
            '<table><tr><th>Project</th><th>Active Δ</th><th>High claim cycles Δ</th>'
            '<th>Expired leases Δ</th></tr>' + deltas +
            '</table></div></section><footer>Offline only · no automatic polling '
            'or production mutation</footer></body></html>')


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--previous", type=Path, required=True)
    p.add_argument("--current", type=Path, required=True)
    p.add_argument("--format", choices=("html", "markdown", "json"), default="html")
    p.add_argument("--output", type=Path, help="Create-only report path")
    args = p.parse_args(argv)
    try:
        if args.previous.resolve() == args.current.resolve():
            raise ValueError("previous and current must be distinct snapshots")
        report = compare(read(args.previous, "board"), read(args.current, "board"))
        rendered = (json.dumps(report, indent=2, ensure_ascii=False) + "\n" if
                    args.format == "json" else md(report) if args.format == "markdown"
                    else html(report))
        if args.output:
            write_create_only(args.output, rendered)
        else:
            print(rendered)
        return 0
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print("zSentinel board comparison denied: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

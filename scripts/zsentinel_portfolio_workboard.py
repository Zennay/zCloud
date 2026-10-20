#!/usr/bin/env python3
"""Combine private, bounded zSentinel GitHub workboards into one portfolio view.

No network calls, claims, scheduler, queue writes, CI operations or releases.
Every source remains a separately observed, incomplete snapshot.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from zsentinel_issue_delivery import build_workboard, render_html, render_markdown
from zsentinel_issue_progress import load_private_prior, _source
from zsentinel_product_issue_inbox import ROOT, canonical_projects, collect

MAX_WINDOWS = 6
MAX_PROJECTS = 18
MAX_ITEMS_PER_WINDOW = 24
MAX_OUTPUT_ITEMS = 144
MAX_OUTPUT_PRS = MAX_PROJECTS * 8 + MAX_WINDOWS * 3


def combine_workboards(snapshots, registry, now=None):
    if not isinstance(snapshots, list) or not 1 <= len(snapshots) <= MAX_WINDOWS:
        raise ValueError("combine one to six independent bounded snapshots")
    if not isinstance(registry, dict):
        raise ValueError("canonical registry not found")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("invalid comparison clock")
    now = now.astimezone(timezone.utc)
    project_set = set()
    all_items = []
    coverage = []
    windows = []
    snapshots_at = []
    seen_urls = set()
    existing_prs = []
    seen_pr_links = set()
    for index, snapshot in enumerate(snapshots):
        # Use the exact existing conservative evidence parser. This refuses
        # invented fix, ownership and merge authorization receipts.
        observed, status_map, parsed = _source(snapshot)
        if (not isinstance(snapshot, dict)
                or snapshot.get("schema") != "zsentinel-issue-delivery-board-v1"
                or snapshot.get("writes_performed") is not False
                or snapshot.get("merge_authorized") is not False
                or snapshot.get("dispatch_authorized") is not False
                or snapshot.get("claims_taken") is not False
                or not isinstance(snapshot.get("items"), list)
                or len(snapshot["items"]) > MAX_ITEMS_PER_WINDOW
                or observed < now - timedelta(days=30)
                or observed > now + timedelta(minutes=2)):
            raise ValueError("historical or untrusted source window")
        projects = snapshot["projects"]
        if len(set(projects)) != len(projects):
            raise ValueError("duplicate projects in source window")
        for pid in projects:
            if pid not in registry or pid in project_set:
                raise ValueError("unknown or overlapping project in source windows")
            project_set.add(pid)
        # The shared HTML renderer uses this exact-head request field. Its
        # omission made the existing saved-window HTML path raise KeyError.
        for field, limit in (("issue_source_requests", 3 * len(projects)),
                             ("exact_head_pr_requests", 15),
                             ("qa_excluded", MAX_ITEMS_PER_WINDOW),
                             ("inspected_pr_count", 3)):
            number = snapshot.get(field)
            if type(number) is not int or not 0 <= number <= limit:
                raise ValueError("invalid bounded per-window GitHub receipt")
        snapshots_at.append(observed)
        age = int(max(0, (now - observed).total_seconds()))
        windows.append({
            "index": index + 1,
            "projects": list(projects),
            "observed_at_utc": observed.isoformat(),
            "age_seconds": age,
            "stale_after_10_minutes": age > 600,
            "source_coverage": {pid: status_map[pid] for pid in projects},
            "source_complete": False,
            "issue_count": len(parsed),
            "owned_work_verified": False,
        })
        for cov in snapshot["coverage"]:
            if cov.get("project") not in projects:
                raise ValueError("unexpected project coverage in source window")
            coverage.append({**cov, "source_observed_at_utc": observed.isoformat(),
                             "source_age_seconds": age,
                             "stale_after_10_minutes": age > 600})
        for row in snapshot["items"]:
            pid = row["project_id"]
            prefix = "https://github.com/" + registry[pid] + "/issues/"
            if not row["issue_url"].startswith(prefix):
                raise ValueError("issue mismatched with canonical repository")
            if row["issue_url"] in seen_urls:
                raise ValueError("duplicate issue identity")
            seen_urls.add(row["issue_url"])
            # Strip previous comparisons and Notion-derived actions, which
            # are source-specific and cannot be re-authorized by aggregation.
            cleaned = dict(row)
            cleaned.pop("notion_active_work_min", None)
            cleaned["source_window"] = index + 1
            cleaned["source_observed_at_utc"] = observed.isoformat()
            cleaned["source_age_seconds"] = age
            cleaned["source_stale"] = age > 600
            all_items.append(cleaned)
        listed = snapshot.get("recent_open_product_prs", [])
        if type(listed) is not list or len(listed) > 8 * len(projects) + 3:
            raise ValueError("unbounded existing PR source list")
        for pr in listed:
            if type(pr) is not dict:
                raise ValueError("invalid listed PR receipt")
            pid = pr.get("project_id")
            url = pr.get("url")
            if (pid not in projects
                    or type(url) is not str
                    or not url.startswith(
                        "https://github.com/" + registry[pid] + "/pull/")
                    or not url.rsplit("/", 1)[-1].isdigit()
                    or pr.get("ownership_verified") is not False
                    or pr.get("merge_authorized") is not False
                    or pr.get("current_exact_head_verified") is not False
                    or type(pr.get("draft")) is not bool
                    or type(pr.get("title")) is not str
                    or not 0 < len(pr["title"]) <= 180
                    or type(pr.get("listed_head_sha")) is not str
                    or len(pr["listed_head_sha"]) != 40
                    or any(ch not in "0123456789abcdef"
                           for ch in pr["listed_head_sha"])
                    or url in seen_pr_links):
                raise ValueError("untrusted or duplicate existing PR candidate")
            seen_pr_links.add(url)
            existing_prs.append({
                **pr, "source_window": index + 1,
                "source_observed_at_utc": observed.isoformat(),
                "source_age_seconds": age, "source_stale": age > 600,
            })
    if (not 1 <= len(project_set) <= MAX_PROJECTS
            or len(all_items) > MAX_OUTPUT_ITEMS
            or len(existing_prs) > MAX_OUTPUT_PRS):
        raise ValueError("portfolio scope exceeds explicit display capacity")

    # P0 first but distribute issue slots fairly within each priority, instead
    # of letting the first sampled project drown out other product issues.
    priority = {"P0": 0, "P1": 1, "P2": 2, "Unspecified": 3}
    grouped = defaultdict(list)
    for item in all_items:
        if (type(item.get("priority")) is not str or
                item["priority"] not in priority):
            raise ValueError("untrusted issue priority")
        grouped[(item["priority"], item["project_id"])].append(item)
    projects = [project for snapshot in snapshots for project in snapshot["projects"]]
    ordered = []
    for severity in ("P0", "P1", "P2", "Unspecified"):
        offset = 0
        while True:
            progressed = False
            for project in projects:
                entries = grouped[(severity, project)]
                if offset < len(entries):
                    ordered.append(entries[offset])
                    progressed = True
            if not progressed:
                break
            offset += 1
    # Cross-window merge-first ordering preserves a project's source and
    # never promotes an uninspected or historical listing into merge proof.
    gate = "bounded_exact_head_observed_not_authorized"
    existing_prs.sort(key=lambda row: (
        row.get("gate_observation") != gate,
        row["draft"], row["source_window"], row["project_id"], row["url"],
    ))
    oldest = min(snapshots_at)
    newest = max(snapshots_at)
    counts = {
        pid: {
            "issues_sampled": sum(row["project_id"] == pid for row in ordered),
            "owner_only_sampled": sum(
                row["project_id"] == pid and row.get("next_step") ==
                "authorized_infrastructure_owner_required" for row in ordered),
            "active_owner_verified": False,
        } for pid in projects
    }
    return {
        "schema": "zsentinel-portfolio-workboard-v1",
        "observed_at": oldest.isoformat(),
        "newest_source_observed_at_utc": newest.isoformat(),
        "projects": projects,
        "source_windows": windows,
        "source_window_count": len(windows),
        "coverage": coverage,
        "project_summary": counts,
        "issue_count": len(ordered),
        "product_issue_count": sum(
            row.get("next_step") != "authorized_infrastructure_owner_required"
            for row in ordered),
        "human_owner_issue_count": sum(
            row.get("next_step") == "authorized_infrastructure_owner_required"
            for row in ordered),
        "items": ordered,
        "recent_open_product_prs": existing_prs,
        "recent_open_product_pr_count": len(existing_prs),
        "merge_first_source_summary": {
            "sampled_open_product_prs": len(existing_prs),
            "observed_exact_head_without_merge_authority": sum(
                pr.get("gate_observation") == gate for pr in existing_prs),
            "uninspected_or_moved_head": sum(
                pr.get("gate_observation") != gate for pr in existing_prs),
            "merge_authorized": False, "owner_claim_verified": False,
        },
        "source_budget_maximum": sum(
            s.get("source_budget_maximum", 0) for s in snapshots),
        "issue_source_requests": sum(s["issue_source_requests"] for s in snapshots),
        "exact_head_pr_requests": sum(s["exact_head_pr_requests"] for s in snapshots),
        "qa_excluded": sum(s["qa_excluded"] for s in snapshots),
        "inspected_pr_count": sum(s["inspected_pr_count"] for s in snapshots),
        "issue_sample_truncated": any(s.get("issue_sample_truncated") is True
                                      for s in snapshots),
        # Recent timestamp != successfully read GitHub source. Degraded or
        # unavailable windows must never advertise current source coverage.
        "all_projects_currently_read": all(
            w["age_seconds"] <= 600 and
            all(status == "partial" for status in w["source_coverage"].values())
            for w in windows),
        "project_sources_degraded_or_unavailable": [
            pid for w in windows for pid, status in w["source_coverage"].items()
            if status != "partial"
        ],
        "full_portfolio_observed": False,
        "full_issue_inventory_known": False,
        "ownership_verified": False,
        "claims_taken": False,
        "dispatch_authorized": False,
        "merge_authorized": False,
        "deploy_authorized": False,
        "writes_performed": False,
    }



def collect_live_workboards(registry, projects, token=None):
    """One explicit, bounded round across canonical repositories.

    Reuse the already-admitted issue and exact-head PR collectors. Each
    three-project window remains its own source observation; no global
    completeness, ownership or merge authority is inferred from aggregation.
    """
    if (not isinstance(registry, dict) or type(projects) is not list
            or not 1 <= len(projects) <= MAX_PROJECTS
            or len(set(projects)) != len(projects)
            or any(type(pid) is not str or pid not in registry for pid in projects)):
        raise ValueError("select one to eighteen distinct canonical live projects")
    reports = []
    provider_budget_exhausted = False
    for start in range(0, len(projects), 3):
        selected = projects[start:start + 3]
        if provider_budget_exhausted:
            # Preserve independently collected earlier windows. For the
            # windows NOT queried after a confirmed provider-wide rate cap,
            # emit explicit UNKNOWN source coverage rather than fabricate an
            # empty GitHub issue/PR backlog or repeat denied requests.
            inbox = {
                "schema": "zsentinel-product-issue-inbox-v1",
                "observed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "projects": selected,
                "coverage": [
                    {"project": pid, "repository": registry[pid],
                     "oldest_window": "not_fetched_after_confirmed_rate_limit",
                     "recent_window": "not_fetched_after_confirmed_rate_limit",
                     "open_pr_window": "not_fetched_after_confirmed_rate_limit",
                     "status": "unavailable", "active_claims_verified": False,
                     "provider_admission": "rate_limit_confirmed"}
                    for pid in selected
                ],
                "request_count": 0, "items": [], "suppressed_qa_only": 0,
                "truncated": True, "stopped_after_rate_or_auth_denial": True,
                "queue_ownership_known": False, "active_claims_verified": False,
                "actions_authorized": False, "fully_synchronized": False,
            }
        else:
            inbox = collect(registry, selected, token, max_items=MAX_ITEMS_PER_WINDOW)
            provider_budget_exhausted = (
                inbox.get("stopped_after_rate_or_auth_denial") is True)
        report = build_workboard(inbox, registry, token=token)
        if (report.get("projects") != selected
                or report.get("claims_taken") is not False
                or report.get("merge_authorized") is not False
                or report.get("writes_performed") is not False
                or type(report.get("issue_source_requests")) is not int
                or type(report.get("exact_head_pr_requests")) is not int
                or report["issue_source_requests"] > 3 * len(selected)
                or report["exact_head_pr_requests"] > 15):
            raise ValueError("invalid or unbounded live source receipt")
        reports.append(report)
    return reports


def main(argv=None):
    p = argparse.ArgumentParser(description="Combine private zSentinel source windows")
    p.add_argument("--registry", type=Path, default=ROOT / "projects.json")
    p.add_argument("--snapshot", type=Path, action="append",
                   help="one through six bounded original JSON files")
    p.add_argument("--collect-live", action="store_true",
                   help="one-shot read-only issue and existing PR scan of canonical project windows")
    p.add_argument("--previous-portfolio", type=Path,
                   help="optional private older portfolio JSON for source-bound PR-change summary")
    p.add_argument("--project", action="append",
                   help="optional canonical project selection for --collect-live; defaults to all")
    p.add_argument("--format", choices=("html", "json", "md"), default="html")
    p.add_argument("--output", type=Path,
                   help="explicit create-only private report output; never overwrites")
    p.add_argument("--queue-overview", type=Path,
                   help="existing local read-only zSentinel portfolio JSON; no queue writes")
    args = p.parse_args(argv)
    try:
        registry = canonical_projects(args.registry)
        if args.collect_live:
            if args.snapshot or args.output is None:
                raise ValueError("live collection requires private --output and no --snapshot")
            selected = args.project if args.project is not None else list(registry)
            snapshots = collect_live_workboards(
                registry, selected, token=os.getenv("GITHUB_TOKEN"))
        else:
            if args.project or not args.snapshot or not 1 <= len(args.snapshot) <= MAX_WINDOWS:
                raise ValueError("provide one to six private snapshots, without --project")
            snapshots = [load_private_prior(path) for path in args.snapshot]
        board = combine_workboards(snapshots, registry)
        if args.previous_portfolio is not None:
            from zsentinel_portfolio_pr_delta import (
                load_previous_portfolio, compare_portfolio_prs,
            )
            older, previous_sha = load_previous_portfolio(args.previous_portfolio)
            board["pr_delta_since_previous_portfolio"] = compare_portfolio_prs(
                board, older, registry, previous_sha)
        board["collection_mode"] = (
            "one_shot_live_read_only" if args.collect_live
            else "explicit_saved_private_sources")
        board["observed_provider_gets"] = (
            board["issue_source_requests"] + board["exact_head_pr_requests"])
        if board["observed_provider_gets"] > 24 * MAX_WINDOWS:
            raise ValueError("combined source request count exceeds bounded budget")
        if args.queue_overview:
            from zsentinel_workboard_queue import read_queue_overview, attach_queue_pressure
            attach_queue_pressure(board, read_queue_overview(args.queue_overview),
                                  registry)
        if args.format == "html":
            output = render_html(board)
        elif args.format == "md":
            output = render_markdown(board)
        else:
            output = json.dumps(board, ensure_ascii=True, sort_keys=True, indent=2)
        if args.output is not None:
            from zsentinel_private_output import write_private_output
            write_private_output(args.output, output, args.format)
        else:
            print(output)
        complete = all(
            row.get("status") == "partial" for row in board["coverage"])
        return 0 if complete and board["all_projects_currently_read"] else 3
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        p.exit(2, "zSentinel portfolio workboard unavailable: " +
               str(exc)[:120] + "\n")


if __name__ == "__main__":
    raise SystemExit(main())

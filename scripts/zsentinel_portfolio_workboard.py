#!/usr/bin/env python3
"""Combine private, bounded zSentinel GitHub workboards into one portfolio view.

No network calls, claims, scheduler, queue writes, CI operations or releases.
Every source remains a separately observed, incomplete snapshot.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from zsentinel_issue_delivery import render_html, render_markdown
from zsentinel_issue_progress import load_private_prior, _source
from zsentinel_product_issue_inbox import ROOT, canonical_projects

MAX_WINDOWS = 6
MAX_PROJECTS = 18
MAX_ITEMS_PER_WINDOW = 24
MAX_OUTPUT_ITEMS = 144


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
    if not 1 <= len(project_set) <= MAX_PROJECTS or len(all_items) > MAX_OUTPUT_ITEMS:
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
        "source_budget_maximum": sum(
            s.get("source_budget_maximum", 0) for s in snapshots),
        "issue_sample_truncated": any(s.get("issue_sample_truncated") is True
                                      for s in snapshots),
        "all_projects_currently_read": all(w["age_seconds"] <= 600 for w in windows),
        "full_portfolio_observed": False,
        "full_issue_inventory_known": False,
        "ownership_verified": False,
        "claims_taken": False,
        "dispatch_authorized": False,
        "merge_authorized": False,
        "deploy_authorized": False,
        "writes_performed": False,
    }


def main(argv=None):
    p = argparse.ArgumentParser(description="Combine private zSentinel source windows")
    p.add_argument("--registry", type=Path, default=ROOT / "projects.json")
    p.add_argument("--snapshot", type=Path, action="append", required=True,
                   help="one through six bounded original JSON files")
    p.add_argument("--format", choices=("html", "json", "md"), default="html")
    p.add_argument("--output", type=Path,
                   help="explicit create-only private report output; never overwrites")
    p.add_argument("--queue-overview", type=Path,
                   help="existing local read-only zSentinel portfolio JSON; no queue writes")
    args = p.parse_args(argv)
    try:
        if not 1 <= len(args.snapshot) <= MAX_WINDOWS:
            raise ValueError("one through six source windows required")
        registry = canonical_projects(args.registry)
        board = combine_workboards(
            [load_private_prior(path) for path in args.snapshot], registry)
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

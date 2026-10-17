"""One-command private zCloud GitHub Activity report for an operator.

This is opt-in source-only CLI tooling, NOT a network service, scheduler,
API handler, browser extension or production SQLite writer. Tokens, raw
branch names and private application data are never logged.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

from scripts.zcloud_activity_errors import ActivitySourceError
from scripts.zcloud_github_activity import GithubReader, collect, portfolio_registry
from scripts.zcloud_activity_ledger import _read, assert_private_destination, merge_and_store
from scripts.zcloud_activity_operator_report import render_html, save_private


def refresh_activity_report(*, registry, project_ids, ledger_path, report_path,
                            offline=False, reader=None):
    """Return bounded operator-safe summary after creating a local HTML file.

    Failures must not erase a previous private ledger/report, even when GitHub
    is rate-limited or the machine is offline. This never authorizes new
    GitHub writes or zCloud live worker mutations.
    """
    ledger = Path(ledger_path)
    report = Path(report_path)
    if ledger.resolve() == report.resolve():
        raise ActivitySourceError("activity_output_paths_must_differ")
    assert_private_destination(ledger)
    assert_private_destination(report)
    if not offline:
        registry_map = portfolio_registry(registry)
        if len(project_ids) > 12 or len(project_ids) < 1:
            raise ActivitySourceError("activity_project_selection_out_of_bounds")
        if len(set(project_ids)) != len(project_ids) or any(pid not in registry_map for pid in project_ids):
            raise ActivitySourceError("activity_project_not_allowlisted")
        source = collect(registry_map, project_ids, reader=reader or GithubReader())
        merge_and_store(ledger, source)
    if not ledger.exists():
        raise ActivitySourceError("activity_private_history_missing")
    cached = _read(ledger)
    if project_ids:
        available = set(cached["coverage"])
        if any(pid not in available for pid in project_ids):
            raise ActivitySourceError("activity_requested_project_not_cached")
        # A project-filtered report stays private and never implicitly
        # includes the other projects held in the same operator-owned ledger.
        if len(project_ids) == 1:
            html = render_html(cached, project=project_ids[0])
        else:
            selected = dict(cached)
            selected["coverage"] = {pid: cached["coverage"][pid] for pid in project_ids}
            html = render_html(selected)
    else:
        html = render_html(cached)
    save_private(report, html)
    coverage = cached["coverage"]
    projects = project_ids or list(coverage)
    return {
        "status": "saved_private_offline_report",
        "project_count": len(projects),
        "partial_projects": [
            pid for pid in projects if coverage[pid]["status"] != "recent_window"
        ],
        "network_fetched": not offline,
        "historical_or_partial": True,
        "live_dashboard_updated": False,
        "production_deployed": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Create private GitHub activity HTML in one command; never touches live zCloud")
    parser.add_argument("--registry", default=str(Path(__file__).resolve().parents[1] / "projects.json"))
    parser.add_argument("--projects", help="Comma-separated canonical project IDs (required unless --offline)")
    parser.add_argument("--ledger", required=True, help="Operator-owned private retained JSON path")
    parser.add_argument("--html", required=True, help="Private offline HTML path outside public/")
    parser.add_argument("--offline", action="store_true", help="Never fetch GitHub; render already stored private data")
    args = parser.parse_args(argv)
    if not args.offline and not args.projects:
        parser.error("--projects is required unless --offline")
    if args.projects is not None and (
            not args.projects or args.projects != args.projects.strip()):
        parser.error("--projects must contain canonical comma-separated IDs")
    selected = args.projects.split(",") if args.projects else []
    outcome = refresh_activity_report(
        registry=args.registry, project_ids=selected,
        ledger_path=args.ledger, report_path=args.html, offline=args.offline,
    )
    summary = ("Private activity HTML saved (not a live dashboard update). "
               + str(outcome["project_count"]) + " projects, "
               + str(len(outcome["partial_projects"])) + " partial.")
    sys.stdout.write(summary + "\n")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ActivitySourceError as exc:
        sys.stderr.write("Private activity report unavailable: " + str(exc) + "\n")
        raise SystemExit(2)

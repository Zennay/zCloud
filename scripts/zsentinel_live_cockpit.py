#!/usr/bin/env python3
"""zSentinel one-command live cockpit: observed queue + bounded public GitHub.

Runs once and exits; does not require a hand-authored evidence bundle. Explicitly
marks Notion/worker claims unavailable rather than inventing ownership. NEVER
mutates production SQLite, a worker, a claim, a runner, or a GitHub action.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from zsentinel_product_issue_inbox import canonical_projects, collect
from zsentinel_queue_observer import observe, explicitly_unobserved_queue
from zsentinel_github_discovery import discover
from zsentinel_recovery_refresh import refresh
from zsentinel_recovery_router import (
    MAX_STATE_BYTES, operator_text, plan, read_json, write_json_atomic, utc_string,
    acquire_private_ledger_lock,
)

ROOT = Path(__file__).resolve().parent.parent
MAX_PROJECTS = 3
MAX_JOBS = 3
HEX_SHA = re.compile(r"^[a-f0-9]{40}$")
UTC = timezone.utc


def parse_jobs(raw, names):
    """Only canonical explicit (project, run, job, SHA) receipt pins."""
    if len(raw) > MAX_JOBS:
        raise ValueError("at most three explicitly pinned job receipts")
    found = {}
    for text in raw:
        if not isinstance(text, str) or len(text) > 180:
            raise ValueError("invalid pinned job receipt length")
        fields = text.split(":")
        if len(fields) != 4:
            raise ValueError("use --job PROJECT:RUN_ID:JOB_ID:COMMIT_SHA")
        pid, run, job, sha = fields
        if (pid not in names or pid in found or not run.isascii()
                or not job.isascii() or not run.isdecimal()
                or not job.isdecimal() or not HEX_SHA.fullmatch(sha)):
            raise ValueError("invalid or duplicate canonical pinned job identity")
        run_id, job_id = int(run), int(job)
        if not (1 <= run_id <= 2**53-1 and 1 <= job_id <= 2**53-1):
            raise ValueError("pinned GitHub IDs out of safe integer range")
        found[pid] = {"run_id":run_id, "job_id":job_id, "head_sha":sha}
    return found


def assemble(registry, selected, jobs, queue_path, token=None,
             include_issues=True, include_ci_discovery=True, observe_vps=False,
             include_notion=False, notion_data_source=None, notion_token=None,
             notion_registry=ROOT / "projects.json", notion_export_path=None, queue_snapshot=None,
             pinned_issues=None, worker_events_database=None):
    if (not isinstance(selected, list) or not 1 <= len(selected) <= MAX_PROJECTS
            or len(selected) != len(set(selected))
            or any(name not in registry for name in selected)):
        raise ValueError("select one to three distinct canonical projects")
    if pinned_issues is None:
        pinned_issues = []
    from zsentinel_pinned_issue import validate_pin_specs, known_owner_gate_specs
    # Validate operator selections FIRST. Add canonical known P0 owner-only
    # issues only when GitHub issue discovery is enabled and cloud is selected;
    # both together remain bounded to at most three exact additional GETs.
    operator_pins = validate_pin_specs(registry, selected, pinned_issues)
    effective_pins = list(pinned_issues)
    auto_added = 0
    if include_issues:
        for raw in known_owner_gate_specs(registry, selected):
            if raw not in effective_pins and len(effective_pins) < 3:
                effective_pins.append(raw)
                auto_added += 1
    validated_pins = validate_pin_specs(registry, selected, effective_pins)
    if validated_pins and not include_issues:
        raise ValueError("pinned P0/P1 issue reads need GitHub issue discovery")
    pins = parse_jobs(jobs, selected)
    discovery = {"source":"not_requested","coverage":[], "request_count":0,
                 "full_repo_ci_inventory":False}
    if include_ci_discovery:
        remaining = [pid for pid in selected if pid not in pins]
        if remaining:
            found, discovery = discover(registry, remaining, token)
            pins.update(found)
    # A portfolio caller may provide the observer's same immutable read-only
    # snapshot. This prevents duplicate live DB sampling within one report.
    queue = observe(queue_path) if queue_snapshot is None else queue_snapshot
    queue_missing = (isinstance(queue, dict)
                     and queue.get("source") == "explicitly_unobserved_no_database")
    if (not isinstance(queue, dict)
            or queue.get("schema") != "zsentinel-queue-pressure-v1"
            or queue.get("claim_ownership_verified") is not False
            or (queue_missing and (
                queue.get("database_read_only") is not False
                or queue.get("queue_inventory_complete") is not False
                or queue.get("totals") != {}
                or queue.get("project_groups") != []
                or queue.get("high_churn_samples") != []
                or queue.get("queue_source_available") is not False
                or queue.get("queue_write_authorized") is not False
                or queue.get("automatic_retry_authorized") is not False))
            or (not queue_missing and (
                queue.get("source") != "local_sqlite_transaction_query_only"
                or queue.get("database_read_only") is not True))):
        raise ValueError("invalid source-bound queue observation")
    observed = datetime.now(UTC)
    # The baseline lacks Notion/worker provenance by design; absence is NOT
    # proof that a task has no owner or that a project is recovered.
    bundle = {
        "schema_version": 1,
        "evidence": {
            "schema_version": 1,
            "observed_at": utc_string(observed),
            "projects": [
                {"id": pid, "notion": {}, "github": pins.get(pid, {}), "runtime": {}}
                for pid in selected
            ],
        },
        "tasks": [], "active_claims": [],
        "task_inventory_complete": False,
        "claim_inventory_complete": False,
        # No SQLite observation must be absent, not represented as a
        # plausible all-zero source in the canonical incident router.
        "queue_pressure": None if queue_missing else queue,
    }
    if queue_missing and observe_vps:
        raise ValueError("host capacity sample needs an existing database mount")
    if observe_vps:
        # Only a deliberate operator invocation on the intended Linux VPS
        # may supply local capacity. This never authorizes a worker or runner.
        from zsentinel_vps_resource_observer import observe_vps_capacity
        bundle["resources"] = observe_vps_capacity(queue_path)
    source = {
        "vps_capacity": ("same_host_readonly_opt_in" if observe_vps
                         else "unknown_not_sampled"),
        "queue": ("explicitly_unobserved_no_database" if queue_missing
                  else "sqlite_readonly_live_observation"),
        "notion": "not_observed_no_ownership_claim",
        "runtime": "not_observed_no_worker_recovery_claim",
        "current_claim_inventory": "unknown_and_not_used_for_routing",
        "max_github_gets": 2*len(pins) + (3*len(selected) if include_issues else 0)
                           + discovery["request_count"] + len(validated_pins),
        "ci_discovery":discovery,
        "github_budget_is_per_invocation_not_global": True,
        "pinned_requested": len(validated_pins),
        "exact_jobs_pinned_requested": len(jobs),
        "auto_owner_gate_pin_count": auto_added,
    }
    if worker_events_database is not None:
        try:
            from zsentinel_worker_progress_observer import observe_worker_progress
            worker = observe_worker_progress(worker_events_database, selected)
            bundle["worker_progress_observation"] = worker
            source["worker_progress"] = worker["coverage"]
        except (OSError, ValueError, sqlite3.Error):
            # Missing/old database affects ONLY this opt-in worker source.
            # Other already collected P0 and issue sources remain usable.
            source["worker_progress"] = "unavailable_no_worker_recovery_proof"
    else:
        source["worker_progress"] = "not_requested"
    if include_notion or notion_export_path is not None:
        # Positive status hints, never current file-scope lease/owner authority.
        try:
            from zsentinel_notion_claim_observer import observe_claims, project_labels
            labels = project_labels(notion_registry, registry)
            if notion_export_path is not None:
                from zsentinel_notion_connector_snapshot import observe_saved_connector
                claims = observe_saved_connector(
                    notion_export_path, notion_data_source, labels)
                source["notion"] = "private_connector_snapshot_unverified_origin"
            else:
                claims = observe_claims(notion_data_source, notion_token, labels)
                source["notion"] = "claim_status_observed_no_file_ownership"
            bundle["notion_claims_observation"] = claims
            source["notion_claim_coverage"] = claims["coverage"]
        except (ValueError, OSError, TypeError):
            source["notion"] = "degraded_unavailable_no_claim_authority"
            source["notion_claim_coverage"] = "unavailable"
    if pins:
        bundle, github = refresh(bundle, registry, token)
        source["github_jobs"] = github
    else:
        source["github_jobs"] = "no_selected_jobs_not_a_zero_incident_claim"
    if include_issues:
        try:
            inbox = collect(registry, selected, token, max_items=40)
            if effective_pins:
                from zsentinel_pinned_issue import read_pinned_issues
                inbox = read_pinned_issues(inbox, registry, selected,
                                           effective_pins, token)
                source["pinned_issue_receipts"] = inbox["pinned_issue_receipts"]
            bundle["issue_inbox"] = inbox
            source["issue_inbox"] = "bounded_readonly"
            source["issue_requests"] = inbox.get("request_count")
        except (OSError, ValueError):
            source["issue_inbox"] = "degraded_unavailable"
    else:
        source["issue_inbox"] = "explicitly_not_requested"
    return bundle, source


def main():
    parser = argparse.ArgumentParser(
        description="One command read-only queue + issue/GitHub zSentinel cockpit")
    parser.add_argument("--database", type=Path,
                        help="existing zCloud SQLite path; query_only/mode=ro")
    parser.add_argument("--github-only", action="store_true",
                        help="run without queue SQLite: claims and queue remain UNKNOWN")
    parser.add_argument("--registry", type=Path, default=ROOT / "projects.json")
    parser.add_argument("--project", action="append", required=True,
                        help="canonical project ID, repeat up to three times")
    parser.add_argument("--job", action="append", default=[],
                        help="optional PROJECT:RUN_ID:JOB_ID:EXACT_HEAD_SHA")
    parser.add_argument("--read-notion-claims", action="store_true",
                        help="read only active Notion work statuses; never prove path ownership")
    parser.add_argument("--notion-data-source",
                        help="Notion queue data source UUID, or NOTION_QUEUE_DATA_SOURCE_ID environment variable")
    parser.add_argument("--notion-claims-export", type=Path,
                        help="private saved Notion connector query; no current-claim proof")
    parser.add_argument("--archive-target", type=Path,
                        help="optional existing external archive mount; metadata plan only")
    parser.add_argument("--archive-min-age-days", type=int, default=7)
    parser.add_argument("--disk-hotspots-root", type=Path,
                        help="explicit workspace for bounded, read-only disk hotspot scan")
    parser.add_argument("--disk-hotspots-output", type=Path,
                        help="create-only PRIVATE .html containing filenames; never stdout")
    parser.add_argument("--disk-sqlite", action="append", default=[],
                        help="optional relative DB filenames; bounded SQLite header/WAL metadata only")
    parser.add_argument("--disk-inspect-unlinked-files", action="store_true",
                        help="opt-in private deleted-but-open files, bounded /proc, no cleanup")
    parser.add_argument("--disk-inspect-open-handles", action="store_true",
                        help="optional private bounded /proc fd metadata; no PID or writer proof")
    parser.add_argument("--disk-hotspots-previous", type=Path,
                        help="opt-in private preceding .json from the same workspace and scanner settings")
    parser.add_argument("--observe-vps-capacity", action="store_true",
                        help="read current Linux host CPU/load/memory and database-mount disk; use ONLY on the intended VPS")
    parser.add_argument("--worker-events-database", type=Path,
                        help="optional existing indexed zCloud history.db: read-only browser-worker progress, no worker claims")
    parser.add_argument("--skip-ci-discovery", action="store_true",
                        help="disable default bounded GitHub queued/failed job discovery")
    parser.add_argument("--skip-github-issues", action="store_true",
                        help="explicitly skip public GitHub issue/PR API reads")
    parser.add_argument("--pin-issue", action="append", default=[],
                        help="extra bounded exact open GitHub issue GET, PROJECT:NUMBER, up to 3 selected projects")
    parser.add_argument("--state", type=Path,
                        help="optional local private incident ledger file")
    parser.add_argument("--format", choices=("text", "json", "html", "ics", "desk"), default="html")
    parser.add_argument("--desk-output", type=Path,
                        help="new, create-only private .html output (required only for --format desk)")
    args = parser.parse_args()
    ledger_lock = None
    try:
        if (args.disk_hotspots_root is None) != (args.disk_hotspots_output is None):
            raise ValueError("disk hotspot scan requires both root and private output")
        if args.archive_target is not None and args.disk_hotspots_root is None:
            raise ValueError("--archive-target requires private disk scan")
        if args.disk_sqlite and args.disk_hotspots_root is None:
            raise ValueError("--disk-sqlite requires root and private disk output")
        if args.disk_inspect_unlinked_files and args.disk_hotspots_root is None:
            raise ValueError("--disk-inspect-unlinked-files requires a private disk scan")
        if args.disk_inspect_open_handles and args.disk_hotspots_root is None:
            raise ValueError("--disk-inspect-open-handles requires a private disk scan")
        if args.disk_hotspots_previous is not None and args.disk_hotspots_root is None:
            raise ValueError("previous disk snapshot requires current private disk observation")
        if args.disk_hotspots_root is not None and args.format not in ("html", "json", "text"):
            raise ValueError("disk hotspot scan supports html, json, text only")
        if args.disk_hotspots_output is not None:
            if args.disk_hotspots_output.suffix != ".html":
                raise ValueError("disk hotspot private output must be .html")
            if any(p is not None and args.disk_hotspots_output.resolve() == p.resolve()
                   for p in (args.state, args.desk_output, args.disk_hotspots_previous)):
                raise ValueError("disk hotspot output overlaps ledger or desk file")
        if args.pin_issue and args.skip_github_issues:
            raise ValueError("--pin-issue cannot be combined with --skip-github-issues")
        if args.read_notion_claims and args.notion_claims_export is not None:
            raise ValueError("choose either live Notion token or saved connector snapshot")
        if args.github_only:
            if args.database is not None:
                raise ValueError("--github-only requires omitting --database")
            if args.observe_vps_capacity:
                raise ValueError("--github-only cannot prove DB-mounted VPS capacity")
        elif args.database is None:
            raise ValueError("--database required unless --github-only is explicit")
        if args.format == "desk" and args.desk_output is None:
            raise ValueError("--format desk requires --desk-output")
        if args.desk_output is not None and args.format not in ("desk", "html"):
            raise ValueError("--desk-output accepts only desk or html operator formats")
        if args.desk_output is not None:
            # Do not create a private HTML report on top of a pending
            # cursor or dedup ledger: a subsequent atomic JSON write could
            # otherwise clobber the report after its exclusive creation.
            private_inputs = [args.state]
            if any(item is not None and args.desk_output.resolve() == item.resolve()
                   for item in private_inputs):
                raise ValueError("desk-output must differ from private ledger/cursor paths")
        if args.state is not None:
            ledger_lock = acquire_private_ledger_lock(args.state)
        registry = canonical_projects(args.registry)
        bundle, provenance = assemble(
            registry, args.project, args.job, args.database,
            token=os.environ.get("GITHUB_TOKEN"),
            include_issues=not args.skip_github_issues,
            include_ci_discovery=not args.skip_ci_discovery,
            observe_vps=args.observe_vps_capacity,
            include_notion=args.read_notion_claims,
            notion_data_source=(args.notion_data_source or
                                os.environ.get("NOTION_QUEUE_DATA_SOURCE_ID")),
            notion_token=os.environ.get("NOTION_TOKEN"),
            notion_registry=args.registry,
            notion_export_path=args.notion_claims_export,
            queue_snapshot=(explicitly_unobserved_queue()
                            if args.github_only else None),
            pinned_issues=args.pin_issue,
            worker_events_database=args.worker_events_database,
        )
        previous = (read_json(args.state, MAX_STATE_BYTES, "state")
                    if args.state and args.state.exists() else None)
        decision, ledger = plan(bundle, previous)
        decision["live_sources"] = provenance
        if "worker_progress_observation" in bundle:
            decision["worker_progress_observation"] = bundle["worker_progress_observation"]
        if "notion_claims_observation" in bundle:
            decision["notion_claims_observation"] = bundle["notion_claims_observation"]
        disk_private_html = None
        if args.disk_hotspots_root is not None:
            from zsentinel_disk_hotspots import observe as disk_observe, html as disk_html, public_summary
            disk_report = disk_observe(args.disk_hotspots_root)
            if args.archive_target is not None:
                from zsentinel_archive_readiness import observe as archive_observe
                disk_report["archive_readiness"] = archive_observe(
                    disk_report, args.archive_target,
                    min_age_days=args.archive_min_age_days)
            if args.disk_sqlite:
                from zsentinel_sqlite_footprint import observe_many as sqlite_observe
                disk_report["sqlite_footprint"] = sqlite_observe(
                    args.disk_hotspots_root, args.disk_sqlite)
            if args.disk_inspect_open_handles:
                from zsentinel_disk_hotspots import attach_open_references
                attach_open_references(disk_report, args.disk_hotspots_root)
            if args.disk_inspect_unlinked_files:
                from zsentinel_disk_hotspots import attach_unlinked_open_files
                attach_unlinked_open_files(disk_report, args.disk_hotspots_root)
            if args.disk_hotspots_previous is not None:
                from zsentinel_disk_trend import compare, load_previous
                disk_report["disk_growth_comparison"] = compare(
                    load_previous(args.disk_hotspots_previous), disk_report)
            disk_private_html = disk_html(disk_report)
            decision["disk_pressure_private_report"] = public_summary(disk_report)
        # Render complete operator output before mutating the optional local
        # ledger. Malformed source or a failed view must not consume incidents.
        decision["private_ledger_written"] = bool(args.state)
        if args.format == "html":
            from zsentinel_recovery_view import html_view
            from zsentinel_pinned_issue import pinned_receipts_html
            from zsentinel_worker_progress_observer import worker_progress_html
            rendered = html_view(decision)
            if "disk_pressure_private_report" in decision:
                from zsentinel_disk_hotspots import public_summary_html
                if rendered.count("<main>") != 1:
                    raise ValueError("incompatible private disk report cockpit HTML")
                rendered = rendered.replace("<main>", "<main>" +
                    public_summary_html(decision["disk_pressure_private_report"]), 1)
            if "worker_progress_observation" in decision:
                if rendered.count("<main>") != 1:
                    raise ValueError("incompatible read-only worker evidence HTML")
                rendered = rendered.replace(
                    "<main>", "<main>" +
                    worker_progress_html(decision["worker_progress_observation"]), 1)
            elif args.worker_events_database is not None:
                rendered = rendered.replace(
                    "<main>", '<main><p class="banner">WORKERBRON ONBESCHIKBAAR'
                    ' · geen bewijs van inactieve of herstelde workers.</p>', 1)
            if provenance.get("pinned_requested"):
                if rendered.count("<main>") != 1:
                    raise ValueError("incompatible pinned-source cockpit HTML")
                rendered = rendered.replace("<main>",
                                            "<main>" + pinned_receipts_html(provenance), 1)
            if args.github_only:
                if rendered.count("<main>") != 1:
                    raise ValueError("incompatible offline cockpit HTML")
                rendered = rendered.replace(
                    "<main>",
                    '<main><p class="banner">GITHUB-ONLY: geen SQLite-wachtrij gelezen.'
                    ' Queue, leases, claims en workerstatus ONBEKEND; geen'
                    ' toestemming voor herstel, merge of release.</p>', 1)
        elif args.format == "json":
            rendered = json.dumps(decision, ensure_ascii=True, sort_keys=True, indent=2)
        elif args.format == "ics":
            from zsentinel_human_agenda import human_calendar
            rendered = human_calendar(decision)
        elif args.format == "desk":
            from zsentinel_incident_desk import review_html
            rendered = review_html(decision)
        else:
            lines = [operator_text(decision)]
            if "disk_pressure_private_report" in decision:
                disk = decision["disk_pressure_private_report"]
                lines.append("SCHIJFRUIMTE: afzonderlijk privé HTML-rapport aangemaakt; "
                             "begrensd gezien minstens %s GiB en %s grote bestanden; "
                             "geen automatische verwijdering of herstelrecht." % (
                             disk["observed_allocated_gib_min"], disk["large_file_candidates_min"]))
            if "worker_progress_observation" in decision:
                from zsentinel_worker_progress_observer import operator_text as worker_text
                lines.insert(0, worker_text(decision["worker_progress_observation"]))
            elif args.worker_events_database is not None:
                lines.insert(0, "WORKERBRON ONBESCHIKBAAR: claims en werkhervatting ONBEKEND.")
            if provenance.get("pinned_requested"):
                from zsentinel_pinned_issue import pinned_receipts_text
                lines.insert(0, pinned_receipts_text(provenance))
            if args.github_only:
                lines.insert(0, "QUEUE: NIET WAARGENOMEN (--github-only); claims en werkhervatting ONBEKEND.")
            if provenance["notion"] in (
                    "claim_status_observed_no_file_ownership",
                    "private_connector_snapshot_unverified_origin"):
                observed = bundle["notion_claims_observation"]
                warning = (" MANUALLY SAVED SNAPSHOT: origin and current "
                           "owner/lease NOT VERIFIED." if provenance["notion"] ==
                           "private_connector_snapshot_unverified_origin" else "")
                lines.append("NOTION: " + str(observed["active_count"]) +
                             " observed Claimed/Running work items; coverage " +
                             observed["coverage"] + "." + warning)
            elif provenance["notion"] == "degraded_unavailable_no_claim_authority":
                lines.append("NOTION: unavailable. No zero-claim assertion.")
            else:
                lines.append("NOTION: not requested.")
            lines.append("WORKER FILE-LEVEL CLAIMS: UNKNOWN. No routing or recovery authorized.")
            rendered = "\n".join(lines)
        if disk_private_html is not None:
            from zsentinel_private_output import write_private_output
            write_private_output(args.disk_hotspots_output, disk_private_html, "html")
        if args.desk_output is not None:
            from zsentinel_incident_desk import write_private_html, review_html
            private_desk = rendered if args.format == "desk" else review_html(decision)
            write_private_html(args.desk_output, private_desk)
        if args.state:
            write_json_atomic(args.state, ledger)
        if args.format != "desk":
            print(rendered)
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as exc:
        parser.exit(2, f"zSentinel live cockpit unavailable: {exc}\n")
    finally:
        if ledger_lock is not None:
            ledger_lock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

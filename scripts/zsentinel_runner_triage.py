#!/usr/bin/env python3
"""One-command zSentinel VPS runner triage for one EXISTING exact GitHub job.

No queue SQLite required. Two GitHub GET requests maximum, one bounded
local /proc snapshot. Never changes GitHub, claims, runners or processes.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from zsentinel_local_runner_observer import observe_local_runner
from zsentinel_recovery_refresh import refresh
from zsentinel_runner_cross_source import combine, as_html, as_text
from zsentinel_private_output import write_private_output

SHA = re.compile(r"^[a-f0-9]{40}$")
REGISTRY = {"cloud": "Zennay/zCloud"}


def observe_exact_job(run_id: int, job_id: int, sha: str, token=None):
    if (type(run_id) is not int or not 1 <= run_id <= 2**53 - 1
            or type(job_id) is not int or not 1 <= job_id <= 2**53 - 1
            or type(sha) is not str or not SHA.fullmatch(sha)):
        raise ValueError("exact existing run/job IDs and 40-character head SHA required")
    source = {
        "schema_version": 1,
        "evidence": {
            "schema_version": 1,
            "projects": [{"id": "cloud", "notion": {}, "runtime": {},
                          "github": {"run_id": run_id, "job_id": job_id,
                                     "head_sha": sha}}],
        },
    }
    _, github = refresh(source, REGISTRY, token=token)
    if (github.get("requests_attempted_max") != 2
            or type(github.get("refreshed_projects")) is not list
            or len(github["refreshed_projects"]) != 1):
        raise ValueError("exact read-only GitHub receipt unavailable")
    local = observe_local_runner()
    combined = combine(local, github["refreshed_projects"])
    return {
        "schema": "zsentinel-single-exact-runner-workbench-v1",
        "runner": combined,
        "github_receipt_coverage": github["github_coverage"],
        "github_get_budget_max": 2,
        "local_listener_processes_observed": local["listener_processes_observed"],
        "local_worker_processes_observed": local["worker_processes_observed"],
        "local_proc_coverage": combined["local_proc_coverage"],
        "repository": REGISTRY["cloud"],
        "runner_registration_verified": False,
        "runner_resumed_verified": False,
        "automatic_retry_authorized": False,
        "runner_restart_authorized": False,
        "queue_or_claims_changed": False,
        "vps_mutations_performed": False,
    }



def observe_discovered_job(token=None):
    """Discover one bounded existing job, refresh its exact identity, then correlate locally."""
    from zsentinel_github_discovery import discover

    discovery = None
    receipts = {}
    def unavailable_discovery():
        return {
            "source": "github_canonical_readonly_bounded_discovery",
            "request_count": None,
            "coverage": [{
                "project_id": "cloud", "status": "degraded_unavailable",
                "authoritative_all_clear": False,
                "additional_job_links": [],
            }],
            "full_repo_ci_inventory": False,
            "jobs_restarted": False,
            "runner_mutations": False,
        }

    try:
        receipts, discovery = discover(REGISTRY, ["cloud"], token=token)
        if (type(receipts) is not dict or type(discovery) is not dict
                or set(receipts) - {"cloud"}):
            raise ValueError("malformed bounded discovery envelope")
        coverage = discovery.get("coverage")
        if type(coverage) is not list or len(coverage) != 1:
            raise ValueError("missing or unbounded cloud discovery coverage")
        row = coverage[0]
        if (type(row) is not dict or row.get("project_id") != "cloud"
                or row.get("status") not in (
                    "selected_one_source_bound_job",
                    "selected_one_source_bound_job_partial_window",
                    "no_actionable_job_in_sampled_windows",
                    "degraded_unavailable")
                or type(row.get("additional_job_links", [])) is not list
                or len(row.get("additional_job_links", [])) > 5):
            raise ValueError("malformed cloud discovery coverage")
        requests = discovery.get("request_count")
        if requests is not None and (type(requests) is not int or not 0 <= requests <= 9):
            raise ValueError("invalid bounded discovery request count")
        if "cloud" in receipts:
            pin = receipts["cloud"]
            if (type(pin) is not dict
                    or type(pin.get("run_id")) is not int
                    or not 1 <= pin["run_id"] <= 2**53 - 1
                    or type(pin.get("job_id")) is not int
                    or not 1 <= pin["job_id"] <= 2**53 - 1
                    or type(pin.get("head_sha")) is not str
                    or not re.fullmatch(r"[a-f0-9]{40}", pin["head_sha"])
                    or row["status"] not in (
                        "selected_one_source_bound_job",
                        "selected_one_source_bound_job_partial_window")):
                raise ValueError("invalid exact cloud job receipt")
        elif row["status"] in (
                "selected_one_source_bound_job",
                "selected_one_source_bound_job_partial_window"):
            raise ValueError("selected cloud job lacks its exact receipt")
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        # Do not turn an inaccessible or malformed source into an empty/healthy inventory.
        receipts = {}
        discovery = unavailable_discovery()

    coverage = discovery.get("coverage")
    row = coverage[0] if isinstance(coverage, list) and coverage else {}
    status = row.get("status")
    if status not in (
            "selected_one_source_bound_job",
            "selected_one_source_bound_job_partial_window",
            "no_actionable_job_in_sampled_windows", "degraded_unavailable"):
        status = "degraded_unavailable"
    if "cloud" in receipts:
        pin = receipts["cloud"]
        report = observe_exact_job(
            pin["run_id"], pin["job_id"], pin["head_sha"], token=token)
        report["github_discovery"] = discovery
        report["github_gets_discovery_observed"] = discovery.get("request_count")
        report["github_get_budget_max"] = (
            discovery["request_count"] + 2
            if type(discovery.get("request_count")) is int else None)
    else:
        local = observe_local_runner()
        # An incomplete discovery window is not evidence that no job exists.
        incomplete = (
            status == "degraded_unavailable"
            or bool(row.get("incomplete_windows"))
            or bool(row.get("not_scanned_windows"))
        )
        receipt = ([{
            "project_id": "cloud", "source": "github_exact_run_job",
            "status": "degraded",
        }] if incomplete else [])
        combined = combine(local, receipt)
        report = {
            "schema": "zsentinel-single-exact-runner-workbench-v1",
            "runner": combined,
            "github_receipt_coverage": status,
            "github_get_budget_max": discovery.get("request_count"),
            "github_gets_discovery_observed": discovery.get("request_count"),
            "local_listener_processes_observed": local["listener_processes_observed"],
            "local_worker_processes_observed": local["worker_processes_observed"],
            "local_proc_coverage": combined["local_proc_coverage"],
            "repository": REGISTRY["cloud"],
            "runner_registration_verified": False,
            "runner_resumed_verified": False,
            "automatic_retry_authorized": False,
            "runner_restart_authorized": False,
            "queue_or_claims_changed": False,
            "vps_mutations_performed": False,
            "github_discovery": discovery,
        }
    # Keep the three-line handoff useful without claiming full-repository coverage.
    detail = " · discovery=" + status
    windows = row.get("incomplete_windows", [])
    if isinstance(windows, list) and windows:
        safe_windows = [x for x in windows if x in ("queued", "in_progress", "failure")]
        if safe_windows:
            detail += " (" + ",".join(safe_windows) + " onvolledig)"
    if len(report["runner"]["status"]) + len(detail) <= 320:
        report["runner"]["status"] += detail
    return report

def html_document(report):
    from html import escape
    if (report.get("schema") != "zsentinel-single-exact-runner-workbench-v1"
            or report.get("runner_restart_authorized") is not False
            or report.get("vps_mutations_performed") is not False):
        raise ValueError("untrusted exact runner report")
    discovery = report.get("github_discovery")
    extra_html = ""
    if discovery is not None:
        if (type(discovery) is not dict
                or discovery.get("full_repo_ci_inventory") is not False
                or discovery.get("jobs_restarted") is not False
                or discovery.get("runner_mutations") is not False):
            raise ValueError("untrusted bounded GitHub discovery")
        coverage = discovery.get("coverage")
        if type(coverage) is not list or len(coverage) > 1:
            raise ValueError("untrusted discovery coverage")
        if coverage:
            row = coverage[0]
            if type(row) is not dict or row.get("project_id") != "cloud":
                raise ValueError("untrusted discovery project")
            from zsentinel_runner_cross_source import additional_jobs_html
            extra_html = additional_jobs_html(row.get("additional_job_links", []))
    return (
        '<!doctype html><html lang="nl"><meta charset="utf-8">'
        '<meta name="referrer" content="no-referrer">'
        '<meta http-equiv="Content-Security-Policy" content="default-src &#39;none&#39;;'
        ' style-src &#39;unsafe-inline&#39;; base-uri &#39;none&#39;">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<title>zSentinel · Exacte GitHub-runnertriage</title>'
        '<style>body{font:16px system-ui;max-width:900px;margin:2rem auto;'
        'padding:1rem;line-height:1.6;background:#0b1220;color:#f8fafc}'
        'section{padding:1.2rem;border:1px solid #64748b;border-radius:12px}'
        'a{color:#93c5fd}strong{color:#facc15}</style>'
        '<main><h1>zSentinel · Self-hosted runner</h1>' +
        as_html(report["runner"]) + extra_html +
        '<p>Lokale listenerprocessen aangetroffen: ' +
        str(report["local_listener_processes_observed"]) +
        ' · processample: ' + escape(report["local_proc_coverage"]) + '.</p>'
        + ('<p>GitHub discovery: ' + escape(str(report.get("github_receipt_coverage", "not_requested"))) +
           ' · geobserveerde discovery-GETs: ' +
           escape(str(report.get("github_gets_discovery_observed", "unknown"))) +
           ' · volledige CI-inventaris: nee.</p>'
           if "github_discovery" in report else '')
        + '<p>Geen herstart, CI-retry, claimwijziging of registratiebewijs.</p>'
        + '</main></html>'
    )


def main():
    parser = argparse.ArgumentParser(description="Inspect one exact existing GitHub job and local runner; no mutation")
    parser.add_argument("--discover", action="store_true",
                        help="discover one bounded existing queued/running/failing job before exact refresh")
    parser.add_argument("--run-id", type=int,
                        help="existing exact GitHub Actions run ID (use all three exact pin options together)")
    parser.add_argument("--job-id", type=int,
                        help="existing exact GitHub Actions job ID")
    parser.add_argument("--head-sha",
                        help="full 40-character HEAD SHA from the existing job")
    parser.add_argument("--format", choices=("text", "json", "html"), default="text")
    parser.add_argument("--output", type=Path, help="new private .html or .json (0600, create-only)")
    args = parser.parse_args()
    try:
        pins = (args.run_id, args.job_id, args.head_sha)
        if args.discover:
            if any(value is not None for value in pins):
                raise ValueError("--discover cannot be combined with exact job pin options")
            report = observe_discovered_job(token=os.environ.get("GITHUB_TOKEN"))
        else:
            if any(value is None for value in pins):
                raise ValueError("provide --discover or all of --run-id, --job-id and --head-sha")
            report = observe_exact_job(
                args.run_id, args.job_id, args.head_sha,
                token=os.environ.get("GITHUB_TOKEN"))
        if args.format == "text":
            if args.output is not None:
                raise ValueError("text output is stdout only; never writes a file")
            print(as_text(report["runner"]))
            return 0
        if args.output is None:
            raise ValueError("JSON and HTML require an explicit private create-only output path")
        raw = (html_document(report) if args.format == "html"
               else json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2))
        write_private_output(args.output, raw, args.format)
        print("Private exact-runner report created (no workspace path or token printed).")
        return 0
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        # Never echo provider responses, bearer tokens or private paths.
        parser.exit(2, "zSentinel exact runner observation unavailable; no changes made.\n")


if __name__ == "__main__":
    raise SystemExit(main())

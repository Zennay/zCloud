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

def _attach_host_pressure(report, enabled):
    """Optional local capacity context; never a queue or GitHub mutation."""
    if not enabled:
        return report
    from zsentinel_runner_host_pressure import observe_host_pressure
    try:
        report["host_pressure"] = observe_host_pressure()
        report["host_pressure_coverage"] = "same_host_readonly_observed"
    except (OSError, ValueError, TypeError):
        report["host_pressure"] = None
        report["host_pressure_coverage"] = "unavailable_not_a_healthy_host_signal"
    return report


def _host_pressure_line(report):
    """One validator shared by private HTML and operator text."""
    state, sample = report.get("host_pressure_coverage"), report.get("host_pressure")
    if state is None and sample is None:
        return ""
    if state == "unavailable_not_a_healthy_host_signal" and sample is None:
        return "Hostdruk ONBEKEND; geen capaciteit- of herstartbewijs."
    if state != "same_host_readonly_observed" or not isinstance(sample, dict):
        raise ValueError("untrusted local host pressure evidence")
    from zsentinel_runner_host_pressure import owner_summary
    return owner_summary(sample)



def observe_exact_job(run_id: int, job_id: int, sha: str, token=None,
                      host_pressure=False):
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
    return _attach_host_pressure({
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
    }, host_pressure)


def observe_discovered_job(token=None, host_pressure=False):
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
                "approval_wait_workflows": [],
                "approval_wait_workflows_sampled": 0,
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
                    "approval_wait_workflow_no_job_selected",
                    "degraded_unavailable")
                or type(row.get("additional_job_links", [])) is not list
                or len(row.get("additional_job_links", [])) > 5):
            raise ValueError("malformed cloud discovery coverage")
        requests = discovery.get("request_count")
        if requests is not None and (type(requests) is not int or not 0 <= requests <= 12):
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
            "no_actionable_job_in_sampled_windows",
            "approval_wait_workflow_no_job_selected", "degraded_unavailable"):
        status = "degraded_unavailable"
    # Optional secondary links never invalidate the primary exact-job receipt.
    from zsentinel_runner_cross_source import (
        validate_additional_jobs, validate_approval_workflows,
    )
    try:
        row["additional_job_links"] = validate_additional_jobs(
            row.get("additional_job_links"))
        row["additional_links_coverage"] = "bounded_observed"
    except (ValueError, TypeError, KeyError, AttributeError):
        row["additional_job_links"] = []
        row["additional_links_coverage"] = "degraded_untrusted_receipt"
    try:
        row["approval_wait_workflows"] = validate_approval_workflows(
            row.get("approval_wait_workflows", []))
        row["approval_links_coverage"] = "bounded_observed"
    except (ValueError, TypeError, KeyError, AttributeError):
        row["approval_wait_workflows"] = []
        row["approval_links_coverage"] = "degraded_untrusted_receipt"
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
        if status == "approval_wait_workflow_no_job_selected":
            # GitHub can hold at workflow level before a job/runner exists.
            # Preserve local observation but route approval to its owner.
            combined["problem"] = (
                "GitHub-workflow wacht op toestemming/concurrency; "
                "er is geen exacte runnerjob als herstelkandidaat.")
            combined["next_action"] = (
                "Bevoegde workflow-eigenaar controleert de gekoppelde "
                "approval-run; geen runnerrestart of automatische goedkeuring.")
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
        safe_windows = [x for x in windows if x in ("queued", "in_progress", "waiting", "failure")]
        if safe_windows:
            detail += " (" + ",".join(safe_windows) + " onvolledig)"
    if len(report["runner"]["status"]) + len(detail) <= 320:
        report["runner"]["status"] += detail
    return _attach_host_pressure(report, host_pressure)

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
            from zsentinel_runner_cross_source import (
                additional_jobs_html, approval_workflows_html,
            )
            if row.get("additional_links_coverage") == "degraded_untrusted_receipt":
                extra_html = (
                    '<section><h2>Aanvullende GitHub-jobs</h2>'
                    '<p>ONBEKEND · Ongeldige begrensde joblinks zijn niet weergegeven.</p>'
                    '<p>Geen retry-, merge- of runnerautorisatie.</p></section>')
            else:
                extra_html = additional_jobs_html(row.get("additional_job_links", []))
            if row.get("approval_links_coverage") == "degraded_untrusted_receipt":
                extra_html += (
                    '<section><h2>GitHub-goedkeuringswacht</h2>'
                    '<p>ONBEKEND · Ongeldige bron; geen approval- of '
                    'runnerherstel geautoriseerd.</p></section>')
            else:
                extra_html += approval_workflows_html(
                    row.get("approval_wait_workflows", []))
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
        + ('<p><strong>Read-only VPS headroom:</strong> ' +
           escape(_host_pressure_line(report)) + '</p>'
           if _host_pressure_line(report) else '')
        + '<p>Geen herstart, CI-retry, claimwijziging of registratiebewijs.</p>'
        + '</main></html>'
    )


def main():
    parser = argparse.ArgumentParser(description="Inspect one exact existing GitHub job and local runner; no mutation")
    parser.add_argument("--discover", action="store_true",
                        help="discover bounded queued/running/approval/failed workflow evidence before exact job refresh")
    parser.add_argument("--observe-host-pressure", action="store_true",
                        help="opt-in bounded read-only VPS root disk/inode/memory capacity")
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
            report = observe_discovered_job(
                token=os.environ.get("GITHUB_TOKEN"),
                host_pressure=args.observe_host_pressure)
        else:
            if any(value is None for value in pins):
                raise ValueError("provide --discover or all of --run-id, --job-id and --head-sha")
            report = observe_exact_job(
                args.run_id, args.job_id, args.head_sha,
                token=os.environ.get("GITHUB_TOKEN"),
                host_pressure=args.observe_host_pressure)
        if args.format == "text":
            if args.output is not None:
                raise ValueError("text output is stdout only; never writes a file")
            print(as_text(report["runner"]) +
                  ("\n" + _host_pressure_line(report)
                   if _host_pressure_line(report) else ""))
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

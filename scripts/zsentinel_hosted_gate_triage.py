#!/usr/bin/env python3
"""Exact GitHub-hosted job admission workbench; no billing or CI mutations.

Owner-only evidence for zCloud #1523. Reads ONE immutable run/job pair using
the existing bounded canonical GitHub refresh, never dispatches or retries.
An unstarted failure is not itself evidence of account billing rejection.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from html import escape
from pathlib import Path

from zsentinel_private_output import write_private_output
from zsentinel_recovery_refresh import refresh

SHA = re.compile(r"^[0-9a-f]{40}$")
REPOS = {
    "cloud": "Zennay/zCloud",
    "ikea": "Zennay/ikea-enhanced-app",
}
SCHEMA = "zsentinel-hosted-admission-triage-v1"


def observe(repo_key, run_id, job_id, head_sha, token=None):
    if (repo_key not in REPOS
            or type(run_id) is not int or not 1 <= run_id <= 2**53 - 1
            or type(job_id) is not int or not 1 <= job_id <= 2**53 - 1
            or type(head_sha) is not str or not SHA.fullmatch(head_sha)):
        raise ValueError("canonical target and existing exact run/job SHA required")
    bundle = {
        "schema_version": 1,
        "evidence": {"schema_version": 1,
                     "projects": [{"id": repo_key,
                                   "github": {"run_id": run_id,
                                              "job_id": job_id,
                                              "head_sha": head_sha},
                                   "notion": {}, "runtime": {}}]},
    }
    _, record = refresh(bundle, {repo_key: REPOS[repo_key]}, token=token)
    if (record.get("requests_attempted_max") != 2
            or type(record.get("refreshed_projects")) is not list
            or len(record["refreshed_projects"]) != 1):
        raise ValueError("unbounded or missing GitHub source")
    item = record["refreshed_projects"][0]
    url = ("https://github.com/" + REPOS[repo_key] + "/actions/runs/" +
           str(run_id) + "/job/" + str(job_id))
    source = "unavailable"
    if item.get("status") != "refreshed":
        state = "github_source_unavailable"
        problem = "GitHub-jobmetadata geweigerd of onvoldoende voor classificatie."
        action = "Account-/repositoryeigenaar leest de bestaande run rechtstreeks in GitHub."
    else:
        if (item.get("source") != "github_exact_run_job"
                or item.get("repository") != REPOS[repo_key]
                or item.get("run_id") != run_id or item.get("job_id") != job_id
                or item.get("head_sha") != head_sha
                or item.get("may_restart_runner") is not False
                or item.get("assignment_source_complete") is not True):
            raise ValueError("untrusted exact hosted admission receipt")
        source = "exact_run_job"
        route, status = item.get("runner_route"), item.get("job_status")
        started = item.get("job_start_evidence")
        assigned = item.get("runner_assigned")
        if (route not in ("self_hosted", "hosted_or_custom", "unknown")
                or status not in ("queued", "waiting", "pending", "in_progress", "completed")
                or type(assigned) is not bool
                or started not in ("completed_without_steps", "completed_with_steps",
                                   "unknown_not_proven")):
            raise ValueError("untrusted hosted admission stage")
        conclusion = item.get("conclusion")
        if status == "completed" and conclusion not in (
                "success", "failure", "cancelled", "timed_out", "action_required"):
            raise ValueError("missing trustworthy terminal GitHub conclusion")
        if route == "self_hosted":
            state = "not_hosted_runner_route"
            problem = "De exacte job gebruikt self-hosted labels; geen GitHub-hosted billingbewijs."
            action = "Onderzoek GitHub self-hosted assignment en runnergroep separaat."
        elif route == "unknown":
            state = "runner_route_unverified"
            problem = "GitHub-runnerlabels zijn onbekend; hosted billingroute niet bewezen."
            action = "Controleer de exacte joblabels en provider-admissie bij de bevoegde eigenaar."
        elif status in ("queued", "waiting", "pending"):
            state = "queued_or_approval_not_billing_proof"
            problem = "Job staat nog in wachtrij of approval/concurrency; geen billingfout bewezen."
            action = "Controleer huidige GitHub-admissie en goedkeuring voor deze exacte run."
        elif status == "in_progress":
            state = "started_or_assigned_state_not_billing_proof"
            problem = "Job is in progress; een pre-start hostingweigering is niet aangetoond."
            action = "Wacht op het bestaande runresultaat; start geen extra betaalde job."
        elif status == "completed" and conclusion == "success":
            state = "exact_job_succeeded_not_global_billing_clearance"
            problem = "Exacte job succesvol; dit bewijst geen herstel van accountbrede CI-admissie."
            action = "Behoud het jobbewijs; controleer andere oorspronkelijke mislukte runs apart."
        elif status == "completed" and conclusion == "cancelled":
            state = "cancelled_without_admission_conclusion"
            problem = "Geannuleerde job bewijst geen GitHub-billingweigering."
            action = "Onderzoek de exacte annulering en eigenaar voordat CI opnieuw wordt gestart."
        elif (status == "completed" and conclusion in
                ("failure", "timed_out", "action_required")
                and item.get("no_job_steps") is True and not assigned):
            state = "completed_without_runner_steps_possible_admission"
            problem = "Mislukte job eindigde zonder toegewezen runner of uitgevoerde steps."
            action = ("Accountbevoegde onderzoekt GitHub Actions billing/spending,"
                      " beleidsregels en platformannotaties; oorzaak blijft ONBEKEND.")
        elif status == "completed" and item.get("no_job_steps") is False:
            state = "steps_ran_not_prestart_billing"
            problem = "GitHub meldt uitgevoerde steps; niet de pre-start-weigering uit #1523."
            action = "Laat code-/CI-eigenaar uitsluitend de exacte joblogs beoordelen."
        elif status == "completed" and item.get("job_start_evidence") == "unknown_not_proven":
            state = "completed_start_evidence_unknown"
            problem = "Job is beëindigd, maar bewijs van wel/niet gestarte steps ontbreekt."
            action = "Bevoegd eigenaar bekijkt exacte runannotaties; billingoorzaak onbekend."
        else:
            state = "terminal_job_without_billing_conclusion"
            problem = "Terminale jobstatus bewijst geen oorzaak of algemeen CI-herstel."
            action = "Controleer de exacte terminale jobuitkomst met de bevoegde eigenaar."
    return {
        "schema": SCHEMA,
        "repository": REPOS[repo_key],
        "exact_job_url": url,
        "exact_head_sha": head_sha,
        "source": source,
        "stage": state,
        "problem": problem,
        "next_action": action,
        "status": "ALLEEN-LEZEN · billingoorzaak en herstel niet bewezen",
        "billable_capacity_requested": False,
        "billing_repair_performed": False,
        "billing_block_confirmed": False,
        "workflow_retried": False,
        "runner_restart_authorized": False,
        "merge_or_deploy_authorized": False,
        "claims_changed": False,
    }


def _assert_report(r):
    if (type(r) is not dict or r.get("schema") != SCHEMA
            or any(r.get(key) is not False for key in
                   ("billable_capacity_requested", "billing_repair_performed",
                    "billing_block_confirmed", "workflow_retried",
                    "runner_restart_authorized", "merge_or_deploy_authorized",
                    "claims_changed"))):
        raise ValueError("untrusted hosted admission observation")
    url = r.get("exact_job_url")
    if (type(url) is not str or not re.fullmatch(
            r"https://github[.]com/Zennay/(?:zCloud|ikea-enhanced-app)"
            r"/actions/runs/[1-9][0-9]*/job/[1-9][0-9]*", url)):
        raise ValueError("untrusted exact job source link")
    return r


def as_text(result):
    r = _assert_report(result)
    return "\n".join(("PROBLEEM · " + r["problem"],
                      "ACTIE · " + r["next_action"],
                      "STATUS · " + r["status"] + " · " + r["exact_job_url"]))


def as_html(result):
    r = _assert_report(result)
    return (
        '<!doctype html><html lang="nl"><meta charset="utf-8">'
        '<meta name="referrer" content="no-referrer">'
        '<meta http-equiv="Content-Security-Policy" content="default-src &#39;none&#39;;'
        'style-src &#39;unsafe-inline&#39;;base-uri &#39;none&#39;">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>zSentinel · GitHub-hosted admission</title>'
        '<style>body{font:16px system-ui;max-width:900px;margin:2rem auto;'
        'padding:1rem;line-height:1.6;background:#0b1220;color:#f8fafc}'
        'section{padding:1.2rem;border:1px solid #64748b;border-radius:12px}'
        'a{color:#93c5fd}strong{color:#facc15}</style>'
        '<main><h1>zSentinel · GitHub-hosted admission</h1><section>'
        '<p><strong>PROBLEEM:</strong> ' + escape(r["problem"]) + '</p>'
        '<p><strong>ACTIE:</strong> ' + escape(r["next_action"]) + '</p>'
        '<p><strong>STATUS:</strong> ' + escape(r["status"]) +
        ' · <a rel="noopener noreferrer" href="' +
        escape(r["exact_job_url"], quote=True) + '">Exacte GitHub-job</a></p>'
        '</section><p>Geen CI-retry, GitHub-billingwijziging of runnerherstart.</p>'
        '</main></html>'
    )


def main():
    parser = argparse.ArgumentParser(description="Read-only exact hosted Actions admission triage")
    parser.add_argument("--repository", choices=sorted(REPOS), required=True)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--job-id", type=int, required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--format", choices=("text", "json", "html"), default="text")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.format == "text" and args.output is not None:
            raise ValueError("text is stdout only")
        if args.format != "text" and args.output is None:
            raise ValueError("private output required for HTML and JSON")
        report = observe(args.repository, args.run_id, args.job_id,
                         args.head_sha, os.environ.get("GITHUB_TOKEN"))
        if args.format == "text":
            print(as_text(report))
        else:
            content = (as_html(report) if args.format == "html" else
                       json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2))
            write_private_output(args.output, content, args.format)
            print("Private source-bound hosted admission report created.")
        return 0
    except (OSError, ValueError):
        parser.exit(2, "zSentinel hosted admission source unavailable; no changes made.\n")


if __name__ == "__main__":
    raise SystemExit(main())

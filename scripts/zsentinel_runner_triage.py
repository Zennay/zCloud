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


def html_document(report):
    from html import escape
    if (report.get("schema") != "zsentinel-single-exact-runner-workbench-v1"
            or report.get("runner_restart_authorized") is not False
            or report.get("vps_mutations_performed") is not False):
        raise ValueError("untrusted exact runner report")
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
        as_html(report["runner"]) +
        '<p>Lokale listenerprocessen aangetroffen: ' +
        str(report["local_listener_processes_observed"]) +
        ' · processample: ' + escape(report["local_proc_coverage"]) + '.</p>'
        '<p>Geen herstart, CI-retry, claimwijziging of registratiebewijs.</p>'
        '</main></html>'
    )


def main():
    parser = argparse.ArgumentParser(description="Inspect one exact existing GitHub job and local runner; no mutation")
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--job-id", type=int, required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--format", choices=("text", "json", "html"), default="text")
    parser.add_argument("--output", type=Path, help="new private .html or .json (0600, create-only)")
    args = parser.parse_args()
    try:
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
    except (OSError, ValueError) as exc:
        # Never echo provider responses, bearer tokens or private paths.
        parser.exit(2, "zSentinel exact runner observation unavailable; no changes made.\n")


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Conservative read-only correlation of EXACT zCloud job and local runner evidence.

Uses *existing* GitHub exact-job refresh receipts and the local /proc observer.
Never treats a process as registered, a queued job as a service outage,
a terminal run as listener recovery, or any observation as restart authority.
"""
from __future__ import annotations

from html import escape
from datetime import datetime, timedelta, timezone
import re

SCHEMA = "zsentinel-runner-cross-source-v1"
JOB_SOURCE = "github_exact_run_job"
LOCAL_SCHEMA = "zsentinel-local-runner-observation-v1"



SHA = re.compile(r"^[0-9a-f]{40}$")
FRESH_MINUTES = 5
QUEUED_ACTIONABLE_MINUTES = 30


def _utc_clock(value, field):
    if type(value) is not str or not 1 <= len(value) <= 45:
        raise ValueError(field + ": UTC timestamp required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(field + ": invalid UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError(field + ": UTC clock required")
    return parsed.astimezone(timezone.utc)


def _action(stage, problem, next_action, local, *, pinned=None,
            source_freshness="unknown"):
    result = {
        "schema": SCHEMA,
        "stage": stage,
        "problem": problem,
        "next_action": next_action,
        "status": "ALLEEN-LEZEN · GitHub-runner herstel NIET BEWEZEN · geen serviceactie",
        "local_proc_coverage": (
            "bounded_sample" if local["process_inventory_complete"] else "partial"),
        "source_freshness": source_freshness,
        "exact_zcloud_job_selected": pinned is not None,
        "exact_job_url": (pinned["url"] if pinned else None),
        "head_sha": (pinned["head_sha"] if pinned else None),
        "registered_runner_verified": False,
        "job_resumed_verified": False,
        "restart_authorized": False,
        "workflow_dispatch_authorized": False,
        "claims_changed": False,
        "mutations_performed": False,
    }
    return result


def combine(local, refreshed_projects):
    """Match one exact job with a fresh local snapshot, not merely labels."""
    if (type(local) is not dict or local.get("schema") != LOCAL_SCHEMA
            or local.get("host") != "vps-bb300bba"
            or local.get("runner_scope") != "Zennay/zCloud:zcloud-vps-1"
            or local.get("mutations_performed") is not False
            or local.get("safe_idle_authorized") is not False
            or local.get("runner_restart_authorized") is not False
            or local.get("github_registration_verified") is not False
            or local.get("github_job_assignment_verified") is not False
            or type(local.get("process_inventory_complete")) is not bool
            or type(local.get("listener_seen_in_process_sample")) is not bool
            or type(local.get("listener_processes_observed")) is not int
            or not 0 <= local["listener_processes_observed"] <= 20000
            or local["listener_seen_in_process_sample"] !=
                (local["listener_processes_observed"] > 0)
            or type(refreshed_projects) is not list or len(refreshed_projects) > 8):
        raise ValueError("local runner or exact-job source untrusted")
    now = datetime.now(timezone.utc)
    local_at = _utc_clock(local.get("observed_at_utc"), "local_proc")
    local_age = now - local_at
    matches = [r for r in refreshed_projects
               if type(r) is dict and r.get("project_id") == "cloud"
               and r.get("source") == JOB_SOURCE
               # Degraded refreshes deliberately omit repository and job
               # details. Keep their explicit source failure visible instead
               # of falsely turning them into 'no job was selected'.
               and (r.get("status") == "degraded" or
                    r.get("repository") == "Zennay/zCloud")]
    if len(matches) > 1:
        raise ValueError("ambiguous simultaneous exact zCloud receipts")
    receipt = matches[0] if matches else None
    if not receipt:
        return _action(
            "no_pinned_zcloud_job",
            "Geen exact geverifieerde zCloud GitHub-job naast lokale VPS-procesbron.",
            "Kies een bestaande exacte zCloud run/job om GitHub-toewijzing te beoordelen.",
            local)
    if receipt.get("status") != "refreshed":
        return _action(
            "github_job_unavailable",
            "Actuele GitHub-jobbron voor zCloud is onvolledig of geweigerd.",
            "Bevoegd eigenaar controleert de exacte GitHub-job wanneer leesrechten terug zijn.",
            local)
    if (type(receipt.get("run_id")) is not int or receipt["run_id"] < 1
            or type(receipt.get("job_id")) is not int or receipt["job_id"] < 1
            or type(receipt.get("head_sha")) is not str
            or not SHA.fullmatch(receipt["head_sha"])
            or type(receipt.get("runner_assigned")) is not bool
            or receipt.get("assignment_source_complete") is not True
            or receipt.get("may_restart_runner") is not False):
        raise ValueError("incomplete exact job identity or unsafe source")
    run_id, job_id = receipt["run_id"], receipt["job_id"]
    if run_id > 2**53 - 1 or job_id > 2**53 - 1:
        raise ValueError("unbounded exact job identifier")
    pinned = {
        "url": "https://github.com/Zennay/zCloud/actions/runs/" +
               str(run_id) + "/job/" + str(job_id),
        "head_sha": receipt["head_sha"],
    }
    github_at = _utc_clock(receipt.get("observed_at"), "github_exact_job")
    queued_at = _utc_clock(receipt.get("queued_at"), "github_queue")
    if queued_at > github_at + timedelta(minutes=2):
        raise ValueError("GitHub job created after observation")
    github_age = now - github_at
    if (local_age < -timedelta(minutes=2) or github_age < -timedelta(minutes=2)
            or local_age > timedelta(minutes=FRESH_MINUTES)
            or github_age > timedelta(minutes=FRESH_MINUTES)
            or abs(local_at - github_at) > timedelta(minutes=FRESH_MINUTES)):
        return _action(
            "source_clocks_not_fresh_or_coincident",
            "GitHub-job en VPS-listener zijn niet recent en gelijktijdig onderbouwd.",
            "Haal beide bronnen opnieuw op zonder oude status als herstelbewijs te presenteren.",
            local, pinned=pinned, source_freshness="stale_or_time_disjoint")
    status = receipt.get("job_status")
    route = receipt.get("runner_route")
    assigned = receipt["runner_assigned"]
    if (status not in ("queued", "in_progress", "completed", "waiting", "pending")
            or route not in ("self_hosted", "hosted_or_custom", "unknown")):
        raise ValueError("incomplete canonical job route")
    if status in ("waiting", "pending"):
        return _action(
            "github_approval_or_concurrency_unknown",
            "GitHub-job wacht mogelijk op goedkeuring of concurrency; geen runneruitval bewezen.",
            "Inspecteer exacte approval/concurrency state, niet de VPS-runner herstarten.",
            local, pinned=pinned, source_freshness="bounded_recent")
    if route != "self_hosted":
        return _action(
            "job_not_proven_self_hosted",
            "Exacte job bewijst geen self-hosted zCloud-route.",
            "Controleer GitHub-runnerlabels en admissie; VPS-herstel is niet onderbouwd.",
            local, pinned=pinned, source_freshness="bounded_recent")
    if status == "queued" and assigned is False:
        if github_at - queued_at < timedelta(minutes=QUEUED_ACTIONABLE_MINUTES):
            return _action(
                "queued_recent_not_yet_stalled",
                "Self-hosted job wacht korter dan 30 minuten; nog geen bewezen vastloper.",
                "Behoud de job; inspecteer toewijzing pas na een verse bron bij aanhoudende vertraging.",
                local, pinned=pinned, source_freshness="bounded_recent")
        if not local["process_inventory_complete"]:
            return _action(
                "queued_unassigned_local_source_partial",
                "Self-hosted job ongeassignerd; lokale procesbron is onvoldoende volledig.",
                "Bevoegd eigenaar inspecteert de runnerbron opnieuw zonder automatische herstart.",
                local, pinned=pinned, source_freshness="bounded_recent")
        if local["listener_seen_in_process_sample"]:
            return _action(
                "queued_unassigned_listener_observed",
                "Self-hosted job ongeassignerd; lokaal is een listenerproces gezien.",
                "Runner-eigenaar onderzoekt GitHub-registratie, labels, runnergroep en repo-toegang.",
                local, pinned=pinned, source_freshness="bounded_recent")
        return _action(
            "queued_unassigned_no_listener_sampled",
            "Self-hosted job ongeassignerd; geen listener in volledige lokale processteekproef.",
            "Bevoegd eigenaar onderzoekt runner-service en registratie, zonder automatische herstart.",
            local, pinned=pinned, source_freshness="bounded_recent")
    if assigned:
        return _action(
            "job_assignment_observed_only",
            "GitHub vermeldt een runner-toewijzing, maar geen bewezen herstel.",
            "Controleer exacte jobuitkomst en actuele GitHub-registratie afzonderlijk.",
            local, pinned=pinned, source_freshness="bounded_recent")
    return _action(
        "nonqueued_self_hosted_unknown",
        "Self-hosted job heeft in deze toestand geen toegewezen runner.",
        "Controleer actuele GitHub-toestand; concludeer niet dat de listener uitgevallen is.",
        local, pinned=pinned, source_freshness="bounded_recent")


def as_html(result):
    if (type(result) is not dict or result.get("schema") != SCHEMA
            or result.get("restart_authorized") is not False
            or result.get("claims_changed") is not False
            or result.get("mutations_performed") is not False):
        raise ValueError("untrusted runner correlation")
    for key in ("problem", "next_action", "status"):
        if type(result.get(key)) is not str or not 1 <= len(result[key]) <= 320:
            raise ValueError("untrusted runner correlation detail")
    link = result.get("exact_job_url")
    if link is not None and (type(link) is not str or not re.fullmatch(
            r"https://github[.]com/Zennay/zCloud/actions/runs/[1-9][0-9]*/job/[1-9][0-9]*",
            link)):
        raise ValueError("invalid exact GitHub job link")
    job_link = (' · <a rel="noopener noreferrer" href="' +
                escape(link, quote=True) + '">Exacte GitHub-job</a>') if link else ''
    return (
        '<section aria-label="zSentinel exacte runnerbron correlatie">'
        '<h2>P0 runner · exacte job plus lokaal proces</h2>'
        '<p><strong>PROBLEEM:</strong> ' + escape(result["problem"]) + '</p>'
        '<p><strong>ACTIE:</strong> ' + escape(result["next_action"]) + '</p>'
        '<p><strong>STATUS:</strong> ' + escape(result["status"]) + job_link + '</p>'
        '</section>'
    )


def as_text(result):
    if result.get("schema") != SCHEMA or result.get("restart_authorized") is not False:
        raise ValueError("invalid read-only runner handoff")
    return "\n".join((
        "RUNNER · " + result["problem"],
        "ACTIE · " + result["next_action"],
        "STATUS · " + result["status"] +
        (" · exact GitHub: " + result["exact_job_url"]
         if result.get("exact_job_url") else ""),
    ))

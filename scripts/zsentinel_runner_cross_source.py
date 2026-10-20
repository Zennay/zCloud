#!/usr/bin/env python3
"""Conservative read-only correlation of EXACT zCloud job and local runner evidence.

Uses *existing* GitHub exact-job refresh receipts and the local /proc observer.
Never treats a process as registered, a queued job as a service outage,
a terminal run as listener recovery, or any observation as restart authority.
"""
from __future__ import annotations

from html import escape

SCHEMA = "zsentinel-runner-cross-source-v1"
JOB_SOURCE = "github_exact_run_job"
LOCAL_SCHEMA = "zsentinel-local-runner-observation-v1"


def combine(local, refreshed_projects):
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
            or not isinstance(refreshed_projects, list)
            or len(refreshed_projects) > 8):
        raise ValueError("local runner or exact-job source untrusted")
    matches = [r for r in refreshed_projects
               if type(r) is dict and r.get("project_id") == "cloud"
               and r.get("repository") == "Zennay/zCloud"
               and r.get("source") == JOB_SOURCE]
    if len(matches) > 1:
        raise ValueError("ambiguous simultaneous exact zCloud receipts")
    receipt = matches[0] if matches else None
    stage = "no_pinned_zcloud_job"
    problem = "Geen exact geverifieerde zCloud GitHub-job naast lokale VPS-procesbron."
    next_action = "Kies een bestaande exacte zCloud run/job om GitHub-toewijzing te beoordelen."
    if receipt is not None and receipt.get("status") != "refreshed":
        stage = "github_job_unavailable"
        problem = "Actuele GitHub-jobbron voor zCloud is onvolledig of geweigerd."
        next_action = "Bevoegd eigenaar controleert de exacte GitHub-job wanneer leesrechten terug zijn."
    elif receipt is not None:
        status = receipt.get("job_status")
        route = receipt.get("runner_route")
        assigned = receipt.get("runner_assigned")
        if (status not in ("queued", "in_progress", "completed", "waiting", "pending")
                or route not in ("self_hosted", "hosted_or_custom", "unknown")
                or type(assigned) is not bool
                or receipt.get("assignment_source_complete") is not True
                or receipt.get("may_restart_runner") is not False):
            raise ValueError("incomplete or authorizing GitHub job receipt")
        if status in ("waiting", "pending"):
            stage = "github_approval_or_concurrency_unknown"
            problem = "GitHub-job wacht op een mogelijke goedkeuring of concurrency; geen runneruitval bewezen."
            next_action = "Inspecteer exacte approval/concurrency state, niet de VPS-runner herstarten."
        elif route != "self_hosted":
            stage = "job_not_proven_self_hosted"
            problem = "Exacte job bewijst geen self-hosted zCloud-route."
            next_action = "Controleer GitHub-runnerlabels en admissie; VPS-herstel is niet onderbouwd."
        elif status == "queued" and assigned is False:
            if local["process_inventory_complete"] and local["listener_seen_in_process_sample"]:
                stage = "queued_unassigned_listener_observed"
                problem = "Self-hosted job ongeassignerd; lokaal is een listenerproces gezien."
                next_action = ("Runner-eigenaar verifieert GitHub-registratie, labels, "
                               "runnergroep en repo-toegang; listenerproces is geen registratiebewijs.")
            elif local["process_inventory_complete"]:
                stage = "queued_unassigned_no_listener_sampled"
                problem = "Self-hosted job ongeassignerd; geen listener in complete lokale processteekproef."
                next_action = ("Runner-eigenaar controleert service/registratie met eigen bevoegdheden; "
                               "geen automatische herstart.")
            else:
                stage = "queued_unassigned_local_source_partial"
                problem = "Self-hosted job ongeassignerd; lokaal /proc onvoldoende volledig."
                next_action = "Ververs onafhankelijke runnerbron onder bevoegde eigenaar; geen restartbesluit."
        elif assigned:
            stage = "job_assignment_observed_only"
            problem = "GitHub vermeldt een runner-toewijzing voor deze job, geen herstelbewijs."
            next_action = "Controleer exact jobresultaat en actuele registratie apart."
        else:
            stage = "nonqueued_self_hosted_unknown"
            problem = "Self-hosted job heeft geen actuele toegewezen runner in deze toestand."
            next_action = "Controleer exacte GitHub-jobtoestand; ga niet uit van runneruitval."
    return {
        "schema": SCHEMA,
        "stage": stage,
        "problem": problem,
        "next_action": next_action,
        "status": "ALLEEN-LEZEN · GitHub-runner herstel NIET BEWEZEN · geen serviceactie",
        "local_proc_coverage": (
            "bounded_sample" if local["process_inventory_complete"] else "partial"),
        "exact_zcloud_job_selected": receipt is not None and receipt.get("status") == "refreshed",
        "registered_runner_verified": False,
        "job_resumed_verified": False,
        "restart_authorized": False,
        "workflow_dispatch_authorized": False,
        "claims_changed": False,
        "mutations_performed": False,
    }


def as_html(result):
    if (type(result) is not dict or result.get("schema") != SCHEMA
            or result.get("restart_authorized") is not False
            or result.get("claims_changed") is not False
            or result.get("mutations_performed") is not False):
        raise ValueError("untrusted runner correlation")
    for key in ("problem", "next_action", "status"):
        if type(result.get(key)) is not str or not 1 <= len(result[key]) <= 320:
            raise ValueError("untrusted runner correlation detail")
    return (
        '<section aria-label="zSentinel exacte runnerbron correlatie">'
        '<h2>P0 runner · exacte job plus lokaal proces</h2>'
        '<p><strong>PROBLEEM:</strong> ' + escape(result["problem"]) + '</p>'
        '<p><strong>ACTIE:</strong> ' + escape(result["next_action"]) + '</p>'
        '<p><strong>STATUS:</strong> ' + escape(result["status"]) + '</p>'
        '</section>'
    )


def as_text(result):
    if result.get("schema") != SCHEMA or result.get("restart_authorized") is not False:
        raise ValueError("invalid read-only runner handoff")
    return "\n".join((
        "RUNNER · " + result["problem"],
        "ACTIE · " + result["next_action"],
        "STATUS · " + result["status"],
    ))

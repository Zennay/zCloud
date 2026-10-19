#!/usr/bin/env python3
"""Source-bound blocker-first zSentinel operator handoff.

The flying fixer must deal with blocked existing workers first, without
mistaking a queued job, lease expiration or a green PR for runtime recovery.
This deliberately never executes a recovery action or authorizes a claim.
"""
from __future__ import annotations

import re
from html import escape

PROJECT = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
INCIDENT = re.compile(r"^[a-f0-9]{32}$")
PRIORITY = {"P0": 0, "P1": 1}


def _non_authorizing(summary, decision, issues):
    if (not isinstance(summary, dict)
            or summary.get("schema") != "zsentinel-portfolio-overview-v1"
            or summary.get("actions_performed") is not False
            or summary.get("queue_owner_authorized") is not False
            or not isinstance(decision, dict)
            or decision.get("schema_version") != 1
            or decision.get("mutations_performed") is not False
            or decision.get("deployment_authorized") is not False
            or decision.get("queue_claims_changed") is not False
            or decision.get("watch_delivery_performed") is not False
            or not isinstance(issues, dict)
            or issues.get("schema") != "zsentinel-next-problems-v1"
            or issues.get("dispatch_authorized") is not False
            or issues.get("writes_performed") is not False):
        raise ValueError("untrusted or authorizing zSentinel source")
    rows = summary.get("rows")
    if not isinstance(rows, list) or len(rows) > 18:
        raise ValueError("unbounded canonical portfolio rows")
    known = set()
    for row in rows:
        pid = row.get("project_id") if isinstance(row, dict) else None
        if (not isinstance(pid, str) or not PROJECT.fullmatch(pid)
                or pid in known):
            raise ValueError("invalid or duplicate registered project")
        known.add(pid)
    return known


def blocker_first_board(summary, decision, issues, pr_desk=None):
    """Produce a bounded *operator* sequence, not a dispatch/release plan.

    Stalled incident cards are source observations. Missing incident coverage,
    historical ledger entries and queue churn never count as worker-resume proof.
    Product fallback candidates are advisory until independent owner/paths/gates
    are reconfirmed by the runtime owner at the moment of action.
    """
    known = _non_authorizing(summary, decision, issues)
    repo_for = {}
    for project_row in summary["rows"]:
        canonical = project_row.get("github_repo")
        if (type(canonical) is not str
                or not re.fullmatch(r"Zennay/[A-Za-z0-9_.-]{1,100}", canonical)):
            raise ValueError("untrusted canonical repository mapping")
        repo_for[project_row["project_id"]] = canonical
    from zsentinel_problem_picker import MAX_NEXT
    incidents = decision.get("incidents")
    backlog = decision.get("historical_unresolved")
    suggestions = decision.get("independent_task_suggestions")
    problem_items = issues.get("items")
    owner_held_issues = issues.get("owner_only_issue_candidates")
    if (not isinstance(owner_held_issues, list)
            or len(owner_held_issues) > 8
            or not isinstance(incidents, list) or len(incidents) > 200
            or not isinstance(backlog, dict)
            or backlog.get("schema") != "zsentinel-historical-backlog-v1"
            or backlog.get("action_authorized") is not False
            or not isinstance(backlog.get("shown"), list)
            or len(backlog["shown"]) > 30
            or type(backlog.get("total_unresolved")) is not int
            or not isinstance(suggestions, list) or len(suggestions) > 5
            or not isinstance(problem_items, list) or len(problem_items) > MAX_NEXT):
        raise ValueError("untrusted bounded blockers or fallback source")
    cards = []
    seen = set()
    for incident in incidents:
        if not isinstance(incident, dict):
            raise ValueError("invalid incident")
        pid, ident, severity = (incident.get("project_id"),
                                incident.get("incident_id"),
                                incident.get("severity"))
        if (pid not in known or not isinstance(ident, str)
                or not INCIDENT.fullmatch(ident) or ident in seen
                or severity not in PRIORITY
                or incident.get("status") not in (
                    "human_action_required", "read_only_diagnosis_required")
                or type(incident.get("human_escalation")) is not bool):
            raise ValueError("invalid or duplicate active blocker")
        seen.add(ident)
        action, owner, problem = (incident.get("action"), incident.get("owner"),
                                  incident.get("problem"))
        if not all(isinstance(value, str) and 1 <= len(value) <= 600
                   and all(ord(ch) >= 32 for ch in value)
                   for value in (action, owner, problem)):
            raise ValueError("untrusted incident action or owner")
        cards.append({
            "kind": "verified_source_incident_not_runtime_recovery",
            "id": ident, "project_id": pid, "priority": severity,
            "problem": problem, "owner": owner, "action": action,
            "observed_from": "bounded_fresh_decision",
            "requires_human": incident["human_escalation"],
            "next_gate": ("authorized_owner_action"
                          if incident["human_escalation"]
                          else "read_only_diagnosis_in_free_scope"),
            "worker_resumed_verified": False, "permission_to_recover": False,
            "owner_claim_verified": False,
        })
    cards.sort(key=lambda item: (PRIORITY[item["priority"]], item["project_id"], item["id"]))
    live_observed_count = len(cards)
    # Explicitly open owner-only P0/P1 GitHub issues are blockers, not free
    # product work. This is an issue-status observation, *not* evidence that
    # the VPS listener is currently offline or that private billing failed.
    issue_gate_count = 0
    held_urls = set()
    for held in owner_held_issues:
        if not isinstance(held, dict):
            raise ValueError("invalid owner-only issue record")
        pid, priority, url = (held.get("project_id"), held.get("priority"),
                              held.get("issue_url"))
        if (pid not in known or priority not in PRIORITY
                or type(url) is not str or url in held_urls
                or not re.fullmatch(r"https://github[.]com/" +
                                    re.escape(repo_for[pid]) +
                                    r"/issues/[1-9][0-9]{0,8}", url)
                or held.get("dispatch_authorized") is not False
                or held.get("next_step") != "authorized_infrastructure_owner_required"):
            raise ValueError("untrusted owner-only issue gate")
        held_urls.add(url)
        cards.append({
            "kind": "open_owner_only_issue_not_current_outage_proof",
            "id": "owner-issue:" + pid + ":" + url.rsplit("/", 1)[1],
            "project_id": pid, "priority": priority,
            "problem": "Open infrastructuurblokkade; actuele oorzaak/runnergezondheid niet bewezen",
            "owner": "authorized_infrastructure_or_account_owner",
            "action": "Bevoegde eigenaar beoordeelt dit bestaande open P0/P1-issue; geen ongeautoriseerde serviceherstart, CI-retry of betaalactie.",
            "observed_from": "bounded_open_github_issue_only",
            "source_owner_issue_url": url,
            "requires_human": True,
            "next_gate": "authorized_infrastructure_owner_required",
            "worker_resumed_verified": False, "permission_to_recover": False,
            "owner_claim_verified": False,
        })
        issue_gate_count += 1
    # Existing source PRs can unblock actual product delivery without creating
    # duplicate implementation. PR metadata is a bounded non-authorizing
    # source, never a file lease, branch protection or review approval.
    pr_gate_count = 0
    if pr_desk is not None:
        from zsentinel_pr_delivery import NEXT_ACTION_NL
        if (not isinstance(pr_desk, dict)
                or pr_desk.get("schema") != "zsentinel-pr-delivery-desk-v1"
                or pr_desk.get("merge_authorized") is not False
                or pr_desk.get("writes_performed") is not False
                or not isinstance(pr_desk.get("items"), list)
                or len(pr_desk["items"]) > 3):
            raise ValueError("untrusted PR delivery desk")
        for pr in pr_desk["items"]:
            if not isinstance(pr, dict):
                raise ValueError("untrusted PR blocker")
            pid, number = pr.get("project_id"), pr.get("pr_number")
            if (pid not in known or type(number) is not int
                    or not 1 <= number <= 100000000
                    or pr.get("repository") != repo_for[pid]
                    or pr.get("source") != "github_exact_pr_readonly"
                    or pr.get("merge_authorized") is not False
                    or pr.get("next_step") not in NEXT_ACTION_NL):
                raise ValueError("invalid existing PR owner/source")
            url = ("https://github.com/" + repo_for[pid] +
                   "/pull/" + str(number))
            if pr.get("url") != url:
                raise ValueError("noncanonical existing PR URL")
            complete = pr.get("coverage") == "bounded_exact_head_review_and_checks"
            head = pr.get("head_sha") if complete else None
            if head is not None and (type(head) is not str or
                                    not re.fullmatch(r"[a-f0-9]{40}", head)):
                raise ValueError("untrusted exact-head PR source")
            check_details = []
            approval_count = None
            if complete:
                ci = pr.get("ci")
                legacy = pr.get("legacy_status_contexts")
                reviews = pr.get("reviews")
                if (not isinstance(ci, dict) or ci.get("head_sha") != head
                        or not isinstance(legacy, dict)
                        or legacy.get("head_sha") != head
                        or not isinstance(reviews, dict)
                        or reviews.get("head_sha") != head):
                    raise ValueError("unmatched exact-head PR gate evidence")
                for origin, receipt in (("check", ci), ("legacy", legacy)):
                    for state in ("failed", "pending", "inconclusive"):
                        names = receipt.get(state, []) if origin == "check" or state != "inconclusive" else []
                        if (not isinstance(names, list) or len(names) > 8
                                or any(type(name) is not str or not 1 <= len(name) <= 140
                                       or any(ord(ch) < 32 for ch in name)
                                       for name in names)):
                            raise ValueError("untrusted PR check status evidence")
                        for name in names:
                            check_details.append(origin + ":" + state + ":" + name)
                approval_count = reviews.get("same_head_approvals_seen")
                if (type(approval_count) is not int
                        or not 0 <= approval_count <= 30):
                    raise ValueError("untrusted bounded PR reviewer count")
            cards.append({
                "observed_check_gates": check_details[:8],
                "observed_check_gates_more": len(check_details) > 8,
                "same_head_approvals_seen_not_policy": approval_count,
                "kind": "existing_product_pr_gate_inspection_only",
                "id": "pr:" + pid + ":" + str(number),
                "project_id": pid, "priority": "P1",
                "problem": "Bestaande product-PR wacht op afrondingscontrole",
                "owner": "authorized_pr_and_release_owner",
                "action": (NEXT_ACTION_NL[pr["next_step"]] if complete else
                           "PR-bron onvolledig; hercontroleer exacte HEAD, eigenaar en vereiste checks."),
                "observed_from": ("bounded_exact_head_pr_gate_not_required_ci_proof"
                                  if complete else "incomplete_pr_source"),
                "source_pr_url": url,
                "source_pr_head": head,
                "requires_human": True,
                "next_gate": pr["next_step"] if complete else "verify_pr_source_with_owner",
                "worker_resumed_verified": False, "permission_to_recover": False,
                "owner_claim_verified": False,
            })
            pr_gate_count += 1
    # Real browser-worker event gaps are useful existing-work blockers. They
    # are never equivalent to a verified active claim or failed product task.
    worker_gap_count = 0
    workers = decision.get("worker_progress_observation")
    if workers is not None:
        if (not isinstance(workers, dict)
                or workers.get("schema") != "zsentinel-worker-progress-observation-v1"
                or workers.get("source") != "existing_sqlite_mode_ro_query_only_indexed_runner_events"
                or workers.get("worker_resumed_verified") is not False
                or workers.get("live_worker_claims_verified") is not False
                or workers.get("actions_performed") is not False
                or not isinstance(workers.get("entries"), list)
                or len(workers["entries"]) > 24):
            raise ValueError("untrusted worker progress blocker source")
        worker_flags = {
            "recent_client_block_event_owner_review",
            "generating_but_progress_unverified_or_stale",
            "worker_event_stale_or_unknown",
            "configured_running_no_event_evidence",
        }
        for worker in workers["entries"]:
            if not isinstance(worker, dict):
                raise ValueError("untrusted individual worker event")
            pid, slot, state = (worker.get("project_id"),
                                worker.get("worker_slot"),
                                worker.get("classification"))
            if (pid not in known or type(slot) is not int
                    or not 1 <= slot <= 16
                    or worker.get("owner_claim_verified") is not False
                    or worker.get("worker_resumed_verified") is not False
                    or worker.get("repair_authorized") is not False):
                raise ValueError("unverified worker event identity or authority")
            desired = worker.get("desired_state")
            if desired not in ("running", "paused", "draining", "unknown"):
                raise ValueError("invalid desired-state source")
            # No active global slot == no active worker to resume.
            # Stale configured rows are intentionally retained as read-only
            # evidence but must never become a P1 incident recommendation.
            global_slot = worker.get("allocated_global_slot")
            if global_slot is not None and (type(global_slot) is not int
                                            or not 1 <= global_slot <= 32):
                raise ValueError("untrusted worker allocation slot")
            if (desired != "running" or global_slot is None
                    or state not in worker_flags):
                continue
            age = worker.get("event_age_seconds")
            if age is not None and (type(age) is not int or age < 0):
                raise ValueError("invalid browser worker event age")
            cards.append({
                "kind": "read_only_worker_progress_gap_not_proven_outage",
                "id": "worker:" + pid + ":" + str(slot),
                "project_id": pid, "priority": "P1",
                "problem": ("Actief ingestelde browserworker " + str(slot) +
                            " · bronvoortgang ontbreekt of clientblokkade mogelijk" +
                            (" · laatste event " + str(age) + " sec geleden"
                             if age is not None else " · eventtijd onbekend")),
                "owner": "authorized_browser_worker_and_queue_owner",
                "action": "Worker is volgens huidige SQLite-desired_state running; verifieer alsnog actuele taakclaim, browserstatus en werkelijke generatievoortgang. Geen force-push, lease reset of serviceherstart op alleen dit event.",
                "observed_from": "local_indexed_worker_event_metadata_no_claim_proof",
                "requires_human": False,
                "next_gate": "verify_exact_worker_claim_and_progress",
                "worker_resumed_verified": False, "permission_to_recover": False,
                "owner_claim_verified": False,
            })
            worker_gap_count += 1
    # A private old incident is not a currently active outage, but still must
    # remain visible before ordinary work; missing from a window != resolved.
    for row in backlog["shown"]:
        if not isinstance(row, dict):
            raise ValueError("invalid historical incident")
        pid, ident = row.get("project_id"), row.get("incident_id")
        if not isinstance(ident, str) or not INCIDENT.fullmatch(ident):
            raise ValueError("untrusted historical identity")
        if pid not in known:
            # Old project was removed/renamed: still counted as unresolved
            # in total_unresolved, not silently promoted to a current owner.
            continue
        if ident in seen:
            continue
        seen.add(ident)
        # Keep exact *historical* GitHub links as a one-click owner review
        # path; a link itself cannot assert the old job remains blocked.
        historic_job = row.get("exact_github_job")
        historic_link = None
        if historic_job is not None:
            if (not isinstance(historic_job, dict)
                    or historic_job.get("project_id") != pid
                    or any(type(historic_job.get(key)) is not int
                           or not 1 <= historic_job[key] <= 2**53-1
                           for key in ("run_id", "job_id"))
                    or type(historic_job.get("head_sha")) is not str
                    or not re.fullmatch(r"[0-9a-f]{40}", historic_job["head_sha"])):
                raise ValueError("untrusted historical job link")
            historic_link = (
                "https://github.com/" + repo_for[pid] +
                "/actions/runs/" + str(historic_job["run_id"]) +
                "/job/" + str(historic_job["job_id"]))
        cards.append({
            "source_job_url_historical_only": historic_link,
            "kind": "historical_not_reconciled", "id": ident,
            "project_id": pid,
            "priority": row.get("severity") if row.get("severity") in PRIORITY else "P1",
            "problem": "Historisch probleem, huidige bron niet geverifieerd",
            "owner": "authorized_source_owner",
            "action": "Hercontroleer exact oude job/claim-bron met eigenaar. Niet herstarten of vrijgeven op afwezigheid.",
            "observed_from": "private_historical_ledger_only",
            "requires_human": True,
            "next_gate": "source_reconciliation",
            "worker_resumed_verified": False, "permission_to_recover": False,
            "owner_claim_verified": False,
        })
    historical_count = (len(cards) - live_observed_count - pr_gate_count
                        - issue_gate_count - worker_gap_count)
    # A historical incident may reference a project removed from today's
    # canonical registry. Do not crash the whole workboard or silently mark
    # that earlier incident recovered; surface it as unmapped source debt.
    unseen_historical_count = max(
        0, backlog["total_unresolved"] - historical_count
    )
    # A useful dashboard must distinguish an unobserved queue from zero.
    # Churn/expired leases are only potential blockers, not incident proof.
    for row in summary["rows"]:
        if not isinstance(row, dict):
            raise ValueError("invalid portfolio row")
        churn = row.get("high_claim_attempts")
        leases = row.get("expired_owner_leases")
        if churn is None or leases is None:
            continue
        if (type(churn) is not int or type(leases) is not int
                or churn < 0 or leases < 0):
            raise ValueError("invalid queue observation counters")
        if churn + leases <= 0:
            continue
        if row.get("queue_observation") != "complete_active_rows":
            continue
        pid = row["project_id"]
        cards.append({
            "kind": "queue_pressure_not_proven_outage",
            "id": "queue:" + pid, "project_id": pid, "priority": "P1",
            "problem": ("Hoge claimpogingen ≥" + str(churn) +
                        "; mogelijk verlopen lease-rijen ≥" + str(leases)),
            "owner": "authorized_queue_owner",
            "action": "Inspecteer huidige queue- en leasehouder voordat bestaande retries worden hervat; geen automatische release, reset of claim.",
            "observed_from": "read_only_complete_active_queue_aggregate",
            "requires_human": True, "next_gate": "verify_exact_lease_and_owner",
            "worker_resumed_verified": False, "permission_to_recover": False,
            "owner_claim_verified": False,
        })
    # Fresh incident and queue/PR evidence must not be starved by dozens
    # of historical, possibly obsolete cards. This is a triage order, NOT
    # any evidence that a source is resolved, owned or safe to mutate.
    order = {
        "verified_source_incident_not_runtime_recovery": 0,
        "open_owner_only_issue_not_current_outage_proof": 1,
        "read_only_worker_progress_gap_not_proven_outage": 2,
        "queue_pressure_not_proven_outage": 3,
        "existing_product_pr_gate_inspection_only": 4,
        "historical_not_reconciled": 5,
    }
    cards.sort(key=lambda c: (
        PRIORITY[c["priority"]],
        order[c["kind"]],
        c["project_id"],
        c["id"],
    ))
    # No unbounded operator page; the *counts* retain the truth of truncation.
    shown = cards[:12]
    fallback = []
    for row in suggestions:
        if (not isinstance(row, dict) or row.get("project_id") not in known
                or row.get("priority") not in ("P0", "P1", "P2", "P3")
                or type(row.get("id")) is not str
                or len(row["id"]) > 100):
            raise ValueError("untrusted independent work suggestion")
        fallback.append({
            "kind": "potential_independent_task_requires_current_claim_recheck",
            "project_id": row["project_id"], "priority": row["priority"],
            "source_task_id": row["id"],
            "next_gate": "recheck_exact_owner_files_and_required_ci",
            "claim_taken": False, "dispatch_authorized": False,
        })
    if not fallback:
        for row in problem_items:
            if (not isinstance(row, dict) or row.get("project_id") not in known
                    or row.get("priority") not in ("P0", "P1", "P2", "Unspecified")
                    or row.get("dispatch_authorized") is not False):
                raise ValueError("untrusted fallback product issue")
            url = row.get("issue_url")
            if (type(url) is not str or
                    not re.fullmatch(r"https://github[.]com/" +
                                     re.escape(repo_for[row["project_id"]]) +
                                     r"/issues/[1-9][0-9]{0,8}", url)):
                raise ValueError("untrusted cross-repository issue URL")
            fallback.append({
                "kind": "product_issue_inspection_only",
                "project_id": row["project_id"], "priority": row["priority"],
                "issue_url": url,
                "next_gate": "recheck_existing_pr_owner_files_and_required_ci",
                "claim_taken": False, "dispatch_authorized": False,
            })
    return {
        "schema": "zsentinel-blocker-first-board-v1",
        "first_action": ("unblock_or_reconcile_existing_work" if cards
                         else "refresh_missing_blocker_sources_before_all_clear"),
        "live_incident_observations": live_observed_count,
        "historical_not_reconciled": historical_count,
        "historical_unmapped_or_unshown": unseen_historical_count,
        "existing_pr_gate_observations": pr_gate_count,
        "owner_only_open_issue_observations": issue_gate_count,
        "worker_event_gap_observations": worker_gap_count,
        "queue_pressure_observations": len(cards) - live_observed_count - historical_count - pr_gate_count - issue_gate_count - worker_gap_count,
        "shown_blocker_cards": shown,
        "blocker_cards_truncated": len(cards) > len(shown),
        "independent_fallback_inspection": fallback[:5],
        "known_complete_blocker_inventory": False,
        "worker_resumed_verified": False,
        "claims_taken": False, "retries_performed": False,
        "deployment_authorized": False, "writes_performed": False,
    }


def blocker_board_html(board):
    """Insert before backlog cards in the existing offline, network-free page."""
    if (not isinstance(board, dict)
            or board.get("schema") != "zsentinel-blocker-first-board-v1"
            or board.get("worker_resumed_verified") is not False
            or board.get("writes_performed") is not False
            or board.get("claims_taken") is not False
            or board.get("deployment_authorized") is not False):
        raise ValueError("untrusted blocker-first board")
    cards = []
    for row in board["shown_blocker_cards"]:
        name = escape(str(row["project_id"])[:45])
        tag = escape(str(row["priority"])[:8])
        what = escape(str(row["problem"])[:320])
        action = escape(str(row["action"])[:550])
        owner = escape(str(row["owner"])[:100])
        basis = escape(str(row["observed_from"])[:90])
        job_link = row.get("source_job_url_historical_only")
        pr_link = row.get("source_pr_url")
        owner_issue_link = row.get("source_owner_issue_url")
        owner_issue_source = (
            '<p><a href="' + escape(owner_issue_link, quote=True) +
            '" target="_blank" rel="noopener noreferrer">Open bestaande P0/P1 eigenaarblokkade</a>'
            ' · issue OPEN in beperkte GitHub-steekproef, geen actuele outage bewezen.</p>'
            if isinstance(owner_issue_link, str) else '')
        pr_source = (
            '<p><a href="' + escape(pr_link, quote=True) +
            '" target="_blank" rel="noopener noreferrer">Bekijk bestaande PR en exacte checks</a>'
            ' · merge/review/release NIET geautoriseerd.</p>'
            if isinstance(pr_link, str) else '')
        gate_details = row.get("observed_check_gates")
        gate_preview = (
            '<p class="small">Waargenomen checks op bron-HEAD ' +
            escape(str(row.get("source_pr_head") or "ONBEKEND")[:12]) +
            ': ' + escape(", ".join(gate_details[:5])) +
            (' · meer checks niet getoond' if row.get("observed_check_gates_more") else '') +
            ' · head-approvals in steekproef: ' +
            escape(str(row.get("same_head_approvals_seen_not_policy"))) +
            ' (GEEN branch protection of reviewtoestemming).</p>'
            if isinstance(gate_details, list) and gate_details else '')
        historical_source = (
            '<p><a href="' + escape(job_link, quote=True) +
            '" target="_blank" rel="noopener noreferrer">Bekijk exact historisch GitHub-jobbewijs</a>'
            ' (geen actuele herstelstatus).</p>'
            if isinstance(job_link, str) else '')
        cards.append(
            '<article class="panel warn"><h3>' + tag + ' · ' + name +
            '</h3><p><strong>' + what + '</strong></p><p>Actie: ' +
            action + '</p><p class="muted">Eigenaar: ' + owner +
            ' · bron: ' + basis +
            ' · worker/runner hervat: NIET bewezen.</p>' +
            historical_source + owner_issue_source + pr_source + gate_preview + '</article>')
    if not cards:
        cards = ['<p class="empty">Geen actuele blockerkaart in deze begrensde'
                 ' bron. Dat bewijst GEEN gezonde werknemers of complete backlog.</p>']
    fallbacks = []
    for row in board["independent_fallback_inspection"]:
        issue_link = row.get("issue_url")
        if isinstance(issue_link, str):
            issue_label = (' · <a href="' + escape(issue_link, quote=True) +
                           '" target="_blank" rel="noopener noreferrer">Open bestaand productissue</a>')
        else:
            issue_label = ''
        fallbacks.append('<li>' + escape(str(row["project_id"])[:45]) +
                         ' · ' + escape(str(row["priority"])[:12]) +
                         ' · ' + escape(str(row["next_gate"])[:90]) +
                         issue_label + ' · GEEN claim of dispatch</li>')
    return (
        '<section aria-label="Eerst blokkades opheffen">'
        '<h2>1 · Eerst vastgelopen werk vrijmaken</h2>'
        '<p class="banner">Bestaande workers en P0/P1-gates gaan voor de backlog.'
        ' Een bronmoment, verlopen lease of groen CI-signaal is nooit herstelbewijs.'
        ' Alleen geautoriseerde eigenaaracties; geen automatische herstart.</p>'
        '<p class="muted">Nu waargenomen incidenten: ' +
        str(board["live_incident_observations"]) +
        ' · eigenaargebonden P0/P1 issues: ' + str(board["owner_only_open_issue_observations"]) +
        ' · worker-eventgaten: ' + str(board["worker_event_gap_observations"]) +
        ' · bestaande PR-gates: ' + str(board["existing_pr_gate_observations"]) +
        ' · historisch te hercontroleren: ' +
        str(board["historical_not_reconciled"]) +
        ' · meer historisch onbekend/niet getoond: ' +
        str(board["historical_unmapped_or_unshown"]) +
        ' · wachtrijdruk: ' + str(board["queue_pressure_observations"]) +
        (' · meer kaarten niet getoond' if board["blocker_cards_truncated"] else '') +
        '</p><div class="grid">' + ''.join(cards) + '</div>'
        '<h2>2 · Ondertussen onafhankelijk productwerk</h2>'
        '<p>Alleen na actuele PR-, bestandspad-, eigenaar- en vereiste CI-controle.'
        ' Geen claims of achtergrondtaken uitgezet.</p><ul>' +
        (''.join(fallbacks) if fallbacks else
         '<li>Geen vrij productwerk bewezen binnen deze bronsteekproef.</li>') +
        '</ul></section>'
    )


def blocker_board_text(board):
    if (not isinstance(board, dict)
            or board.get("schema") != "zsentinel-blocker-first-board-v1"
            or board.get("writes_performed") is not False):
        raise ValueError("untrusted blocker board")
    lines = ["EERST BLOKKADES (geen claim/herstel bewezen):"]
    for item in board["shown_blocker_cards"]:
        lines.append("  " + item["priority"] + " " + item["project_id"] +
                     " · " + item["problem"] + " | " + item["next_gate"])
        if item.get("source_owner_issue_url"):
            lines.append("    eigenaar-only bronissue: " + item["source_owner_issue_url"] +
                         " · geen actuele outage bewezen")
        if item.get("observed_check_gates"):
            lines.append("    exacte HEAD " +
                         str(item.get("source_pr_head") or "UNKNOWN")[:12] +
                         " · checkstatus: " +
                         ", ".join(item["observed_check_gates"][:5]) +
                         " · checklijst is NIET de vereiste branchebescherming")
    if not board["shown_blocker_cards"]:
        lines.append("  Bron is begrensd; afwezigheid != hersteld.")
    if board["blocker_cards_truncated"]:
        lines.append("  Meer blockers dan de zichtbare 12; GEEN all-clear.")
    lines.append("DAARNA VRIJ PRODUCTWERK (alleen bronvoorstel):")
    for item in board["independent_fallback_inspection"]:
        lines.append("  " + item["priority"] + " " + item["project_id"] +
                     " | " + item.get("issue_url", "bestaande bron-taak") +
                     " | " + item["next_gate"] + " | geen claim")
    if not board["independent_fallback_inspection"]:
        lines.append("  Geen vrij werk bewezen.")
    return "\n".join(lines)

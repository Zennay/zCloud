#!/usr/bin/env python3
"""zSentinel complete portfolio overview with a strictly bounded live GitHub window.

One invocation covers every canonical project in local queue aggregates, while
only one explicit group of up to three projects gets GitHub issue/CI sampling.
Unobserved GitHub sources remain UNKNOWN; no project is called recovered.
No queue/claim/runner/CI/Notion or production mutation is possible here.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sqlite3
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from urllib.parse import quote

from zsentinel_product_issue_inbox import canonical_projects
from zsentinel_queue_observer import observe, explicitly_unobserved_queue
from zsentinel_live_cockpit import assemble, ROOT, MAX_PROJECTS
from zsentinel_recovery_router import MAX_STATE_BYTES, plan, read_json, write_json_atomic, acquire_private_ledger_lock
from zsentinel_recovery_view import html_view
from zsentinel_problem_picker import next_problems, problems_html, link_existing_pr_actions
from zsentinel_unblock_board import blocker_first_board, blocker_board_html, blocker_board_text
from zsentinel_pinned_issue import pinned_receipts_html, pinned_receipts_text
from zsentinel_worker_progress_observer import worker_progress_html, operator_text as worker_progress_text
from zsentinel_pr_delivery import discover_open_prs, observe_prs, pr_delivery_html
from zsentinel_portfolio_windows import select_next, advance, coverage_history, acquire_window_lock


def project_windows(registry):
    if not isinstance(registry, dict) or not registry:
        raise ValueError("empty canonical project registry")
    # Keep the control-plane at the first known position so the default
    # operator run inspects its P0 issues; the other windows are stable.
    ids = sorted(registry)
    if "cloud" in ids:
        ids.remove("cloud")
        ids.insert(0, "cloud")
    return [ids[i:i+MAX_PROJECTS] for i in range(0, len(ids), MAX_PROJECTS)]


def verified_source_coverage(selected, provenance, bundle):
    """Never confuse an attempted GitHub window with successfully read sources.

    Source admission is explicitly a bounded sample; even a successful read
    never proves all jobs, open PRs or project health.
    """
    result = {}
    ci = provenance.get("ci_discovery")
    ci_rows = ci.get("coverage") if isinstance(ci, dict) else None
    if not isinstance(ci_rows, list):
        ci_rows = []
    exact = provenance.get("github_jobs")
    exact_rows = exact.get("refreshed_projects") if isinstance(exact, dict) else None
    if not isinstance(exact_rows, list):
        exact_rows = []
    inbox = bundle.get("issue_inbox")
    issue_rows = inbox.get("coverage") if isinstance(inbox, dict) else None
    if not isinstance(issue_rows, list):
        issue_rows = []
    for pid in selected:
        ci_status = [
            row.get("status") for row in ci_rows
            if isinstance(row, dict) and row.get("project_id") == pid
        ]
        issue_status = [
            row.get("status") for row in issue_rows
            if isinstance(row, dict) and row.get("project") == pid
        ]
        # A valid selected exact job is useful partial evidence even when
        # GitHub reported extra unexamined workflow/jobs pages or a sibling
        # window failed. Do not conceal this observed incident simply because
        # another part of the bounded source sample is incomplete.
        refreshed = [
            row for row in exact_rows
            if isinstance(row, dict) and row.get("project_id") == pid
            and row.get("source") == "github_exact_run_job"
            and row.get("status") == "refreshed"
        ]
        # Explicitly pinned jobs are NOT present in discovery.coverage.
        # Their exact run/job receipt is stronger partial evidence than an
        # empty auto-discovery window. Never equate this with full CI health.
        ci_ok = (len(refreshed) == 1 or
                 len(ci_status) == 1 and ci_status[0] in (
                     "selected_one_source_bound_job",
                     "selected_one_source_bound_job_partial_window",
                     "no_actionable_job_in_sampled_windows"))
        # GitHub 401/403 can be repository-specific. An unrelated project's
        # denied issue window must not suppress this project's own complete
        # bounded issue/PR receipt. Global rate-limit handling is already
        # reflected in each per-project coverage status.
        issue_ok = (len(issue_status) == 1 and issue_status[0] == "partial")
        # A truncated issue feed is still a partial observation, not an
        # authoritative full issue list. It should not mask a CI denial.
        if ci_ok and issue_ok:
            result[pid] = "partial_both_sources"
        elif ci_ok:
            result[pid] = "ci_sample_only"
        elif issue_ok:
            result[pid] = "issue_sample_only"
        elif ci_status or issue_status or provenance.get("issue_inbox") == "degraded_unavailable":
            result[pid] = "degraded_or_denied"
        else:
            result[pid] = "not_requested_or_unavailable"
    return result


def portfolio_summary(registry, queue, selected, github_coverage, decision):
    """Coverage-aware project rows; zero is never emitted for absent evidence."""
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
                or queue.get("queue_write_authorized") is not False))
            or (not queue_missing and (
                queue.get("source") != "local_sqlite_transaction_query_only"
                or queue.get("database_read_only") is not True))):
        raise ValueError("untrusted or authorizing queue observation")
    totals = queue.get("totals")
    groups = queue.get("project_groups")
    if not isinstance(totals, dict) or not isinstance(groups, list):
        raise ValueError("incomplete queue pressure contract")
    complete = queue.get("queue_inventory_complete") is True
    if (not isinstance(github_coverage, dict) or set(github_coverage) != set(selected)):
        raise ValueError("GitHub source coverage missing for selected window")
    triage = decision.get("open_product_issue_triage", {})
    incidents = decision.get("incidents", [])
    history = decision.get("historical_unresolved", {})
    if (not isinstance(history, dict)
            or history.get("schema") != "zsentinel-historical-backlog-v1"
            or history.get("action_authorized") is not False
            or type(history.get("total_unresolved")) is not int
            or type(history.get("shown")) is not list
            or len(history["shown"]) > 30):
        raise ValueError("missing bounded private historical source backlog")
    issue_items = triage.get("issues", []) if isinstance(triage, dict) else []
    if not isinstance(issue_items, list) or not isinstance(incidents, list):
        raise ValueError("malformed source-bound incident and issue observations")
    summary = {}
    for key, repository in registry.items():
        if not isinstance(key, str) or not isinstance(repository, str):
            raise ValueError("invalid canonical project mapping")
        summary[key] = {
            "project_id": key, "github_repo": repository,
            "github_observation": github_coverage.get(key, "not_queried"),
            "queue_observation": ("not_observed_explicitly" if queue_missing
                                  else "complete_active_rows" if complete
                                  else "partial_unreliable"),
            "queued": 0 if complete else None,
            "owned": 0 if complete else None,
            "high_claim_attempts": 0 if complete else None,
            "expired_owner_leases": 0 if complete else None,
            "unverifiable_owner_leases": 0 if complete else None,
            "inconsistent_owner_lease_windows": 0 if complete else None,
            "worker_ownership_verified": False,
            "recovery_proven": False, "next_action": "read_only_inspect_current_source",
            "p0_p1_issue_sample_hits": None, "p2_unranked_issue_sample_hits": None,
            "incident_sample_hits": None,
            "historical_unresolved_sample_hits": 0,
            "notion_active_status_min": None,
            "notion_verifying_status_min": None,
            "github_job_runner_route": "not_scanned",
            "github_ci_source_stop_reason": None,
            "github_ci_windows_not_scanned": [],
            "github_sampled_actionable_jobs": None,
            "github_sampled_queued_self_hosted_jobs_min": None,
            "github_other_jobs_list_truncated": False,
        }
    for entry in groups:
        if not isinstance(entry, dict):
            raise ValueError("invalid queue group row")
        key = entry.get("project_id")
        if key not in summary:
            # Registered projects only; unknown DB projects count toward
            # global pressure, but are NEVER silently mapped or owned.
            continue
        if complete:
            row = summary[key]
            for source, target in (
                ("queued", "queued"), ("high_claim_attempts", "high_claim_attempts"),
                ("expired_owner_lease_rows", "expired_owner_leases"),
                ("unverifiable_owner_lease_rows", "unverifiable_owner_leases"),
                ("inconsistent_owner_lease_window_rows",
                 "inconsistent_owner_lease_windows"),
            ):
                value = entry.get(source)
                if type(value) is not int or value < 0:
                    raise ValueError("invalid queue counter")
                row[target] += value
            for owned_key in ("claimed", "running", "verifying"):
                value = entry.get(owned_key)
                if type(value) is not int or value < 0:
                    raise ValueError("invalid owned queue counter")
                row["owned"] += value
    live_sources = decision.get("live_sources")
    job_data = live_sources.get("github_jobs") if isinstance(live_sources, dict) else None
    jobs = job_data.get("refreshed_projects") if isinstance(job_data, dict) else []
    if not isinstance(jobs, list) or len(jobs) > 8:
        raise ValueError("invalid bounded GitHub runner route receipts")
    routes_seen = set()
    for item in jobs:
        if not isinstance(item, dict):
            raise ValueError("invalid runner route source")
        pid = item.get("project_id")
        if pid not in selected or pid in routes_seen:
            raise ValueError("duplicate or unselected GitHub runner route")
        routes_seen.add(pid)
        route = (item.get("runner_route") if item.get("status") == "refreshed"
                 else "degraded_unknown")
        if route not in ("self_hosted", "hosted_or_custom", "unknown", "degraded_unknown"):
            raise ValueError("invalid route admission category")
        summary[pid]["github_job_runner_route"] = route
        if item.get("status") == "refreshed":
            observed_status = item.get("job_status")
            if observed_status not in (
                    "queued", "in_progress", "completed", "waiting", "pending"):
                raise ValueError("invalid exact GitHub job status")
            # When this is a manually pinned job there may be no discovery
            # receipt at all; still show the ONE truly observed exact job.
            # A discovery source can later replace this minimum with its
            # independently observed multi-job count.
            summary[pid]["github_sampled_actionable_jobs"] = 1
            summary[pid]["github_sampled_queued_self_hosted_jobs_min"] = int(
                observed_status == "queued" and route == "self_hosted")
        # Refresh only proves the exact selected job; CI-source admission
        # metadata belongs to ci_discovery.coverage, never github_jobs.
    # CI discovery is distinct from exact-job refresh. Read its bounded
    # coverage to surface denied repositories and *all* sampled actionable
    # jobs without mistaking a GitHub runner assignment for healthy runtime.
    discovery = (live_sources.get("ci_discovery")
                 if isinstance(live_sources, dict) else None)
    rows = (discovery.get("coverage") if isinstance(discovery, dict)
            and discovery.get("source") == "github_canonical_readonly_bounded_discovery"
            else [])
    if type(rows) is not list or len(rows) > 3:
        raise ValueError("invalid GitHub discovery coverage source")
    seen_discovery = set()
    for item in rows:
        if type(item) is not dict:
            raise ValueError("invalid GitHub discovery entry")
        pid = item.get("project_id")
        if pid not in selected or pid in seen_discovery:
            raise ValueError("duplicate or unselected CI discovery")
        seen_discovery.add(pid)
        source_stop = item.get("repository_stop_reason")
        windows_skipped = item.get("not_scanned_windows", [])
        if (source_stop not in (
                None, "github_permission_or_rate_limit",
                "github_authentication_required", "github_rate_limit_confirmed",
                "github_forbidden_or_rate_limit_unknown",
                "repository_inaccessible", "github_network_unavailable")
                or type(windows_skipped) is not list or len(windows_skipped) > 3
                or any(w not in ("queued", "in_progress", "failure")
                       for w in windows_skipped)
                or len(set(windows_skipped)) != len(windows_skipped)):
            raise ValueError("invalid canonical GitHub CI stop")
        summary[pid]["github_ci_source_stop_reason"] = source_stop
        summary[pid]["github_ci_windows_not_scanned"] = windows_skipped
        count = item.get("sampled_actionable_jobs")
        if count is None:
            if item.get("additional_job_links"):
                raise ValueError("untrusted additional CI jobs")
            continue
        extras = item.get("additional_job_links", [])
        cut = item.get("additional_jobs_truncated")
        if (type(count) is not int or not 1 <= count <= 150
                or type(extras) is not list or len(extras) > 5
                or type(cut) is not bool
                or cut != (count > len(extras) + 1)
                or item.get("repository") != registry[pid]
                or item.get("primary_job_status") not in ("queued", "completed")
                or item.get("primary_runner_route") not in (
                    "self_hosted", "hosted_or_custom", "unknown")):
            raise ValueError("untrusted multi-job CI inventory")
        self_hosted = int(item["primary_job_status"] == "queued"
                          and item["primary_runner_route"] == "self_hosted")
        for extra in extras:
            if (type(extra) is not dict
                    or extra.get("action_authorized") is not False
                    or extra.get("observed_only") is not True
                    or extra.get("work_resumed_verified") is not False
                    or extra.get("job_status") not in ("queued", "completed")
                    or extra.get("runner_route") not in (
                        "self_hosted", "hosted_or_custom", "unknown")):
                raise ValueError("invalid sampled additional job")
            self_hosted += int(extra["job_status"] == "queued"
                               and extra["runner_route"] == "self_hosted")
        summary[pid]["github_sampled_actionable_jobs"] = count
        summary[pid]["github_sampled_queued_self_hosted_jobs_min"] = self_hosted
        summary[pid]["github_other_jobs_list_truncated"] = cut
    # Counts below are lower bounds in the bounded source window,
    # NEVER total open portfolio issue/incident inventory.
    for pid in selected:
        if github_coverage[pid] in ("partial_both_sources", "issue_sample_only"):
            summary[pid]["p0_p1_issue_sample_hits"] = sum(
                isinstance(item, dict) and item.get("project_id") == pid
                and item.get("priority") in ("P0", "P1") for item in issue_items
            )
            summary[pid]["p2_unranked_issue_sample_hits"] = sum(
                isinstance(item, dict) and item.get("project_id") == pid
                and item.get("priority") in ("P2", "Unspecified") for item in issue_items
            )
        if github_coverage[pid] in ("partial_both_sources", "ci_sample_only"):
            summary[pid]["incident_sample_hits"] = sum(
                isinstance(item, dict) and item.get("project_id") == pid
                for item in incidents
            )
    # Historical ledger counts only, never current GitHub health or claims.
    for incident in history["shown"]:
        if (not isinstance(incident, dict)
                or incident.get("current_source_observed") is not False
                or incident.get("mutation_authorized") is not False):
            raise ValueError("historical incident cannot authorize work")
        pid = incident.get("project_id")
        if pid in summary:
            summary[pid]["historical_unresolved_sample_hits"] += 1
    notion = decision.get("notion_claims_observation")
    status_source_windows = None
    if notion is not None:
        if (not isinstance(notion, dict)
                or notion.get("source") not in (
                    "notion_readonly_data_source_pages",
                    "user_supplied_notion_connector_export_unverified_origin")
                or notion.get("claim_scope_verified") is not False
                or notion.get("can_dispatch") is not False
                or notion.get("coverage") not in (
                    "current_pages_only", "incomplete",
                    "saved_single_page_not_live_current_claims_unknown")
                or (notion.get("source") ==
                    "user_supplied_notion_connector_export_unverified_origin"
                    and (notion.get("origin_authenticity_verified") is not False
                         or notion.get("live_claim_ownership_verified") is not False))
                or not isinstance(notion.get("active_project_counts"), dict)):
            raise ValueError("invalid Notion project statuses")
        counts = notion["active_project_counts"]
        # Native live observation exports one bounded coverage receipt for
        # each status; a manually saved snapshot cannot claim this provenance.
        live_windows = notion.get("status_source_windows")
        if live_windows is not None:
            expected = (("Claimed", 1), ("Running", 1), ("Verifying", 2))
            if (notion["source"] != "notion_readonly_data_source_pages"
                    or type(live_windows) is not list
                    or len(live_windows) != len(expected)):
                raise ValueError("untrusted live Notion per-status windows")
            total_pages = 0
            total_matched = 0
            any_partial = False
            for item, (expected_status, expected_budget) in zip(live_windows, expected):
                if (type(item) is not dict
                        or item.get("status") != expected_status
                        or type(item.get("pages_read")) is not int
                        or not 1 <= item["pages_read"] <= expected_budget
                        or type(item.get("page_budget")) is not int
                        or item["page_budget"] != expected_budget
                        or type(item.get("observed_rows")) is not int
                        or not 0 <= item["observed_rows"] <= item["pages_read"] * 100
                        or item.get("coverage") not in ("partial", "complete_within_source")
                        or item.get("read_only") is not True
                        or item.get("claim_scope_verified") is not False):
                    raise ValueError("invalid Notion source window receipt")
                total_pages += item["pages_read"]
                total_matched += item["observed_rows"]
                any_partial |= item["coverage"] == "partial"
            if (type(notion.get("total_pages_read")) is not int
                    or notion["total_pages_read"] != total_pages
                    or total_pages > 4
                    or notion.get("active_count") != total_matched
                    or (notion.get("coverage") == "incomplete") != any_partial):
                raise ValueError("Notion source-window totals disagree")
            status_source_windows = [dict(item) for item in live_windows]
        verifying = notion.get("verifying_project_counts")
        if verifying is not None and type(verifying) is not dict:
            raise ValueError("invalid Notion verifying-status counts")
        for key, value in counts.items():
            if not isinstance(key, str) or type(value) is not int or value < 0 or value > 400:
                raise ValueError("invalid active project status counter")
        if verifying is not None:
            for key, value in verifying.items():
                if (not isinstance(key, str) or type(value) is not int
                        or value <= 0 or value > counts.get(key, -1)):
                    raise ValueError("Notion verifying work exceeds source totals")
            if (type(notion.get("verifying_count")) is not int
                    or sum(verifying.values()) != notion["verifying_count"]):
                raise ValueError("Notion verifying count contradicts project groups")
        for pid in summary:
            # An absent project in a bounded connector sample is UNKNOWN,
            # never evidence that the project has zero live claims.
            value = counts.get(pid)
            summary[pid]["notion_active_status_min"] = (
                value if value is not None and value > 0 else None)
            # Verifying means unfinished work, not a live worker lease.
            verified = verifying.get(pid) if verifying is not None else None
            summary[pid]["notion_verifying_status_min"] = (
                verified if verified is not None and verified > 0 else None)
    # The incident means only that one exact GitHub job has no runner.
    # It cannot establish a self-hosted route: its labels may be unavailable
    # or a sampled job may differ from the one referenced by the incident.
    unassigned_job_projects = {
        item.get("project_id") for item in incidents
        if isinstance(item, dict) and item.get("problem") == "github_job_unassigned"
    }
    unstarted_job_projects = {
        item.get("project_id") for item in incidents
        if isinstance(item, dict) and item.get("problem") == "github_job_unstarted"
    }
    for row in summary.values():
        if row["project_id"] in unassigned_job_projects:
            row["next_action"] = "github_unassigned_job_route_owner_review"
        elif row["project_id"] in unstarted_job_projects:
            row["next_action"] = "github_job_unstarted_platform_owner_review"
        elif row["github_ci_source_stop_reason"] == "github_rate_limit_confirmed":
            row["next_action"] = "github_rate_limit_backoff_other_projects"
        elif row["github_ci_source_stop_reason"] == "github_authentication_required":
            row["next_action"] = "github_auth_owner_access_review"
        elif row["github_ci_source_stop_reason"] is not None:
            row["next_action"] = "github_ci_source_denied_no_retry_owner_review"
        elif not complete:
            row["next_action"] = "queue_source_incomplete_owner_must_review"
        elif row["inconsistent_owner_lease_windows"]:
            row["next_action"] = "request_inconsistent_claim_window_owner_review"
        elif row["expired_owner_leases"]:
            row["next_action"] = "request_owner_lease_review_never_release"
        elif row["unverifiable_owner_leases"]:
            row["next_action"] = "request_unknown_lease_clock_owner_review"
        elif row["high_claim_attempts"]:
            row["next_action"] = "inspect_claim_churn_no_auto_retry"
        elif row["historical_unresolved_sample_hits"]:
            row["next_action"] = "historical_incident_source_recheck_owner"
        elif row["github_observation"] == "not_queried":
            row["next_action"] = "github_source_not_scanned_next_window"
        elif row["github_observation"] in ("degraded_or_denied", "not_requested_or_unavailable"):
            row["next_action"] = "github_source_unavailable_no_all_clear"
        else:
            row["next_action"] = "inspect_open_issue_pr_and_exact_ci_gates"
    unknown_count = sum(
        1 for entry in groups
        if isinstance(entry, dict) and isinstance(entry.get("project_id"), str)
        and entry["project_id"] not in registry
    )
    return {
        "schema": "zsentinel-portfolio-overview-v1",
        "observed_at_utc": queue.get("observed_at_utc"),
        "canonical_projects": len(registry),
        "github_projects_selected": len(selected),
        "github_projects_observed": sum(
            github_coverage.get(pid) in (
                "partial_both_sources", "ci_sample_only", "issue_sample_only")
            for pid in selected),
        "github_coverage_is_partial": True,
        "queue_inventory_complete": complete,
        "queue_source": ("explicitly_unobserved_no_database" if queue_missing
                         else "local_sqlite_transaction_query_only"),
        "queue_owner_authorized": False,
        "notion_status_source_windows": status_source_windows,
        "unregistered_queue_project_groups": None if queue_missing else unknown_count,
        "rows": list(summary.values()),
        "actions_performed": False,
        "runner_recovered": False,
        "deployment_verified": False,
    }


def _count(value):
    return "ONBEKEND" if value is None else str(value)


def branch_ownership_html(source):
    if source is None:
        return ('<section><h2>Branches zonder PR</h2><p class="empty">'
                'Niet opgevraagd; recente branch-eigenaren blijven onbekend.</p></section>')
    if (not isinstance(source, dict)
            or source.get("schema") != "zsentinel-recent-unpr-branches-v1"
            or source.get("coverage") not in ("bounded_complete", "incomplete_owner_review")
            or source.get("worker_claims_changed") is not False
            or source.get("current_slice_safe_to_claim") is not False
            or not isinstance(source.get("recent_candidates"), list)
            or len(source["recent_candidates"]) > 12):
        raise ValueError("untrusted or authorizing branch observation")
    repo = source.get("repository")
    if (not isinstance(repo, str) or not repo.startswith("Zennay/")
            or repo.count("/") != 1):
        raise ValueError("invalid canonical repository for branch report")
    cards = []
    ordered_branches = sorted(source["recent_candidates"],
                              key=lambda x: (not x.get("potential_overlap", False),
                                             -x.get("ahead_by", 0),
                                             str(x.get("branch", ""))))
    for entry in ordered_branches:
        if (not isinstance(entry, dict) or entry.get("owner_verified") is not False
                or entry.get("claim_authorized") is not False
                or not isinstance(entry.get("branch"), str)
                or not isinstance(entry.get("head_sha"), str)
                or not isinstance(entry.get("paths"), list)
                or len(entry["paths"]) > 100):
            raise ValueError("invalid branch ownership record")
        sha = entry["head_sha"]
        if len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha):
            raise ValueError("invalid branch commit identity")
        linked = ("https://github.com/" + quote(repo, safe="/") +
                  "/tree/" + quote(entry["branch"], safe=""))
        paths = "".join("<li>" + escape(p) + "</li>" for p in entry["paths"])
        cards.append(
            '<article class="panel read"><h3><a target="_blank" '
            'rel="noopener noreferrer" href="' + escape(linked, quote=True) +
            '">' + escape(entry["branch"]) + '</a></h3>'
            '<p class="small">Exact commit: <code>' + sha + '</code> · leeftijd: ' +
            str(entry["commit_age_hours"]) + ' uur · vooruit op main: ' +
            str(entry["ahead_by"]) + ' commits · bestanden: ' +
            str(entry["path_count"]) + '.</p>'
            '<p><strong>' + ("MOGELIJKE PADOVERLAP" if entry["potential_overlap"]
                            else "Geen overlap in opgegeven scope gevonden") +
            '</strong> · actief eigendom NIET bevestigd.</p>'
            '<details><summary>Gewijzigde paden bekijken</summary>'
            + ('<p class="muted">Toont maximaal 100 van ' +
               str(entry["path_count"]) + ' brongevalideerde paden; '
               'overlapanalyse gebruikt het volledige bewijs.</p>'
               if entry.get("paths_truncated_for_view") else '')
            + '<ul>' + paths + '</ul></details></article>'
        )
    return (
        '<section><h2>Recente branches zonder open PR · read-only</h2>'
        '<p class="banner">Brondekking: ' + escape(source["coverage"]) +
        ' · recente kandidaten: ' + str(source["recent_candidate_count"]) +
        ' · mogelijke padbotsingen: ' +
        str(sum(x["potential_overlap"] for x in source["recent_candidates"])) +
        '. Mogelijke botsingen staan bovenaan. '
        'Een oude branch is geen actieve eigenaar; ontbrekende '
        'of afgekapt bronbewijs is nooit bewijs van vrije scope. '
        'Geen automatische claimvrijgave of overname.</p>'
        '<div class="grid">' +
        ("".join(cards) if cards else
         '<p class="empty">Geen recente kandidaten in dit begrensde venster; '
         'geen bewijs van complete eigendomsvrijheid.</p>') +
        '</div></section>'
    )


def phrase_route(value):
    return {
        "self_hosted": "SELF-HOSTED joblabels bevestigd",
        "hosted_or_custom": "Hosted/custom labels, VPS NIET aangetoond",
        "unknown": "Joblabels ONBEKEND",
        "degraded_unknown": "Bronfout: route ONBEKEND",
        "not_scanned": "NIET GESCAND",
    }.get(value) or "ONBEKEND"


def portfolio_html(summary, decision, window_index, windows, branch_inventory=None, problem_inbox=None, pr_desk=None, unblock=None, registry=None):
    if (not isinstance(summary, dict)
            or summary.get("schema") != "zsentinel-portfolio-overview-v1"
            or summary.get("actions_performed") is not False
            or summary.get("queue_owner_authorized") is not False):
        raise ValueError("noncanonical or authorizing portfolio report")
    cells = []
    for row in summary["rows"]:
        name = escape(row["project_id"])
        repo = row["github_repo"]
        if (not isinstance(repo, str) or not repo.startswith("Zennay/")
                or repo.count("/") != 1
                or not all(c.isascii() and (c.isalnum() or c in "_-.") for c in repo.split("/")[1])):
            raise ValueError("untrusted repository link")
        link = "https://github.com/" + escape(repo, quote=True)
        github = {
            "partial_both_sources": "BEPERKTE CI + issues",
            "ci_sample_only": "BEPERKTE CI · issues onbekend",
            "issue_sample_only": "BEPERKTE issues · CI onbekend",
            "degraded_or_denied": "BRONFOUT / geen volledige dekking",
            "not_requested_or_unavailable": "NIET BESCHIKBAAR / geen scan",
            "not_queried": "NIET GESCAND · onbekend",
        }.get(row["github_observation"])
        if github is None:
            raise ValueError("unsupported GitHub coverage class")
        action_text = {
            "queue_source_incomplete_owner_must_review": "Wachtrij incompleet; bron-/ownercontrole nodig",
            "github_unassigned_job_route_owner_review": "P0: exacte GitHub-job en labels door eigenaar controleren; runnerroute ONBEKEND tot dezelfde job is bevestigd",
            "github_job_unstarted_platform_owner_review": "P1: exacte job startte niet; GitHub-checkannotaties door account-/workfloweigenaar beoordelen (oorzaak onbekend)",
            "request_owner_lease_review_never_release": "Vraag leasebeoordeling; niet vrijgeven",
            "request_inconsistent_claim_window_owner_review":
                "Claimstart en vervaltijd spreken elkaar tegen: eigenaar controleert beide, niet herclaimen",
            "request_unknown_lease_clock_owner_review": "Leaseklok ontbreekt/ongeldig; eigenaar controleert claim, niet automatisch vrijgeven",
            "inspect_claim_churn_no_auto_retry": "Onderzoek claimchurn; geen retry",
            "github_source_not_scanned_next_window": "GitHub onbekend; volgend venster",
            "historical_incident_source_recheck_owner": "Historische P0/P1-bron via eigenaar verifiëren; GEEN herstelbewijs",
            "github_source_unavailable_no_all_clear": "Bronfout: opnieuw via geautoriseerde route",
            "github_ci_source_denied_no_retry_owner_review":
                "GitHub CI-bron geweigerd of netwerk onbereikbaar; geen herhaalde retries. Bevoegde API-/repo-eigenaar controleert toegang.",
            "github_rate_limit_backoff_other_projects":
                "GitHub-rate-limit bevestigd; geen extra retries, andere vrije producttaak oppakken.",
            "github_auth_owner_access_review":
                "GitHub API authenticatie ontbreekt (401); bevoegde eigenaar controleert tokenrechten.",
            "branch_overlap_needs_owner": "RECENTE BRANCHOVERLAP: eigenaar + PR-scope controleren",
            "branch_inventory_partial": "PR-loze branchbron onvolledig: geen vrijgave",
            "branch_scope_not_provided": "Geen actuele padscope: overlap onbekend",
            "inspect_open_issue_pr_and_exact_ci_gates": "Bekijk issues, PR en exacte CI",
        }.get(row["next_action"])
        if action_text is None:
            raise ValueError("unsupported portfolio recommendation")
        attempt = row.get("github_last_attempt_at_utc")
        attempt_status = row.get("github_last_attempt_coverage")
        # A historical read is a private source-attempt receipt, not a current
        # API, owner, runner or CI health assertion.
        attempt_label = (
            ("HUIDIGE POGING" if row.get("github_last_attempt_this_run") else "HISTORISCH") +
            " · " + str(row.get("github_last_attempt_age_hours")) + " uur geleden" +
            " · " + str(attempt_status)
            if attempt else "NOOIT IN PRIVÉVENSTER WAARGENOMEN"
        )
        cells.append(
            '<tr><th scope="row"><a href="' + link +
            '" target="_blank" rel="noopener noreferrer">' + name + '</a></th>'
            '<td>' + _count(row["queued"]) + '</td>'
            '<td>' + _count(row["owned"]) + '</td>'
            '<td>' + ('ONBEKEND' if row["notion_active_status_min"] is None
                      else '≥' + str(row["notion_active_status_min"])) +
            ('<br><small>Verifying ≥' + str(row["notion_verifying_status_min"]) +
             ' · eigenaar hercontroleren</small>'
             if row["notion_verifying_status_min"] is not None else '') + '</td>'
            '<td>' + _count(row["high_claim_attempts"]) + '</td>'
            '<td>' + _count(row["expired_owner_leases"]) + '</td>'
            '<td>' + _count(row["unverifiable_owner_leases"]) + '</td>'
            '<td>' + _count(row["inconsistent_owner_lease_windows"]) + '</td>'
            '<td>' + escape(github) + '</td>'
            '<td>' + escape(attempt_label) + '</td>'
            '<td>' + phrase_route(row["github_job_runner_route"]) +
            ('<br><small>CI-BRON GESTOPT: ' +
             escape(row["github_ci_source_stop_reason"]) +
             ' · niet onderzocht: ' +
             escape(", ".join(row["github_ci_windows_not_scanned"]) or "geen") +
             '</small>' if row["github_ci_source_stop_reason"] else '') + '</td>'
            '<td>' + ('ONBEKEND' if row["github_sampled_actionable_jobs"] is None
                      else ('≥' + str(row["github_sampled_actionable_jobs"]) +
                            ' gevonden, waaronder ≥' +
                            str(row["github_sampled_queued_self_hosted_jobs_min"]) +
                            ' wachtende self-hosted' +
                            (' · andere jobs deels verborgen'
                             if row["github_other_jobs_list_truncated"] else ''))) +
            '</td>'
            '<td>' + ('ONBEKEND' if row["p0_p1_issue_sample_hits"] is None
                      else '≥' + str(row["p0_p1_issue_sample_hits"])) + '</td>'
            '<td>' + ('ONBEKEND' if row["p2_unranked_issue_sample_hits"] is None
                      else '≥' + str(row["p2_unranked_issue_sample_hits"])) + '</td>'
            '<td>' + ('ONBEKEND' if row["incident_sample_hits"] is None
                      else '≥' + str(row["incident_sample_hits"])) + '</td>'
            '<td>' + str(row["historical_unresolved_sample_hits"]) + ' / steekproef</td>'
            '<td>' + escape(action_text) + '</td></tr>'
        )
    selected = ", ".join(escape(x) for x in windows[window_index])
    sources = decision.get("live_sources")
    pinned_jobs = sources.get("exact_jobs_pinned_requested", 0) if isinstance(sources, dict) else 0
    if type(pinned_jobs) is not int or not 0 <= pinned_jobs <= 3:
        raise ValueError("untrusted explicit GitHub job selection")
    caption = (
        f'Venster {window_index + 1} van {len(windows)}: <strong>{selected}</strong>. '
        f'Expliciete bestaande GitHub-jobpins: {pinned_jobs} (alleen bron-GET; geen rerun). '
        f'{summary["canonical_projects"]} canonieke projecten zichtbaar; '
        f'{summary["github_projects_observed"]} van '
        f'{summary["github_projects_selected"]} geselecteerde projecten met '
        'daadwerkelijk beperkte GitHub-bronobservatie. '
        'Niet opgevraagd of mislukt betekent niet gezond. Laatste bronpoging '
        'komt alleen uit de optionele privécursor en bewijst nooit actuele CI/gezondheid.'
    )
    notion_mode = sources.get("notion") if isinstance(sources, dict) else None
    if notion_mode == "private_connector_snapshot_unverified_origin":
        caption += (" NOTION HANDMATIGE SNAPSHOT: niet live, bronherkomst NIET "
                    "geverifieerd; positieve actieve projectwaarnemingen "
                    "zijn GEEN actuele lease-, claim- of werkvrijgave.")
    elif notion_mode == "claim_status_observed_no_file_ownership":
        caption += (" NOTION LIVE STATUS: beperkte actieve werkregels; "
                    "geen bestandsscope, owner- of dispatchautorisatie.")
    elif notion_mode == "degraded_unavailable_no_claim_authority":
        caption += (" NOTION ONBESCHIKBAAR: afwezige regels zijn ONBEKEND, "
                    "niet nul actieve claims.")
    # Render the operator's next useful source read as a visible artifact,
    # not just a generic "Notion incomplete" badge. Manual/private snapshots
    # cannot claim live per-status coverage.
    notion_windows = summary.get("notion_status_source_windows")
    notion_coverage_html = (
        '<p class="muted">Notion statusvensters: NIET afzonderlijk live '
        'geverifieerd; opgeslagen of ontbrekende bron verleent geen rechten.</p>'
        if notion_windows is None else
        '<p class="muted">Notion read-only bronvensters: ' +
        " · ".join(
            escape(item["status"]) + " ≥" + str(item["observed_rows"]) +
            " (" + str(item["pages_read"]) + "/" +
            str(item["page_budget"]) + " pagina's; " +
            ("ONVOLLEDIG · eigenaar moet bron herlezen"
             if item["coverage"] == "partial"
             else "afgerond binnen bronvenster")
            for item in notion_windows
        ) + ". GEEN bewijs van actieve lease, claimvrijgave of volledige queue.</p>'
    )
    coverage = (
        "Huidige wachtrijmomentopname is volledig binnen de observerlimiet; "
        "maar er is GEEN geverifieerd bestandseigendom."
        if summary["queue_inventory_complete"] else
        "Wachtrijbron onvolledig: cijfers bewust ONBEKEND, geen nul-melding."
    )
    table = (
        '<section><h2>Volledig portfolio · gedeeltelijke broncontrole</h2>'
        '<p class="muted">' + caption + '</p>' + notion_coverage_html +
        '<p class="banner">' + coverage +
        ' Geen claim, workflow, herstart, lease of deploy uitgevoerd.</p>'
        '<div class="wrap"><table><thead><tr><th scope="col">Project</th>'
        '<th scope="col">Wachtend</th><th scope="col">Bezet</th>'
        '<th scope="col">Notion werkstatus (min.; incl. Verifying)</th>'
        '<th scope="col">Veel claims</th><th scope="col">Lease verlopen?</th>'
         '<th scope="col">Leaseklok onbekend?</th>'
        '<th scope="col">Claimvenster inconsistent?</th>'
        '<th scope="col">GitHub-bereik</th><th scope="col">Laatste bronpoging (geen health)</th><th scope="col">Runnerroute</th>'
        '<th scope="col">GitHub-jobs (steekproef)</th>'
        '<th scope="col">P0/P1-issues (min.)</th>'
        '<th scope="col">P2/zonder label (min.)</th>'
        '<th scope="col">Incidenten (min.)</th><th scope="col">Historisch (steekproef)</th><th scope="col">Veilige vervolgstap</th>'
        '</tr></thead><tbody>' +
        "".join(cells) + '</tbody></table></div>'
        '<p class="muted small">Zonder privécursor kiest <code>--window auto</code> '
        'elk UTC-uur een ander bronvenster. Met <code>--window-state</code> '
        'roteren opeenvolgende succesvolle operatorruns langs de bronvensters. '
        'Een getal ≥0 is alleen een ondergrens van de steekproef, '
        'nooit het totale issue- of incidentenaantal. '
        'Handmatig kiezen: <code>--window 0</code> tot <code>--window ' +
        str(len(windows)-1) +
        '</code>. Eén run scant nooit stiekem alle repositories via GitHub.</p>'
        '</section>'
    )
    raw = html_view(decision, registry=registry)
    if raw.count("<main>") != 1:
        raise ValueError("incompatible underlying offline operator view")
    queue_warning = (
        '<p class="banner">GITHUB-ONLY: lokale SQLite-wachtrij NIET waargenomen. '
        'Alle wachtrij-, claim-, lease- en ownercijfers zijn ONBEKEND, niet nul. '
        'Geen toestemming voor workerherstel of release.</p>'
        if summary.get("queue_source") == "explicitly_unobserved_no_database"
        else "")
    progress = decision.get("worker_progress_observation")
    progress_view = (
        worker_progress_html(progress) if isinstance(progress, dict)
        else '<p class="banner">WORKER-EVENTBRON ONBESCHIKBAAR · geen huidige claim-/workerhervattingsbewijzen.</p>'
             if decision.get("live_sources", {}).get("worker_progress")
             == "unavailable_no_worker_recovery_proof" else "")
    return raw.replace("<main>", "<main>" + queue_warning +
                       pinned_receipts_html(decision.get("live_sources")) +
                       (blocker_board_html(unblock) if unblock is not None else "") +
                       progress_view +
                       (problems_html(problem_inbox) if problem_inbox is not None else "") +
                       (pr_delivery_html(pr_desk) if pr_desk is not None else "") +
                       table + branch_ownership_html(branch_inventory), 1)


def main():
    parser = argparse.ArgumentParser(description="Read-only complete zSentinel portfolio and bounded GitHub window")
    parser.add_argument("--database", type=Path,
                        help="existing zCloud SQLite file (never created or modified)")
    parser.add_argument("--queue-previous", type=Path,
                        help="private 0600 previous queue snapshot (same SQLite file, max 72h); issue #677")
    parser.add_argument("--github-only", action="store_true",
                        help="explicitly run without SQLite: queue/claim status UNKNOWN; only GitHub/optional Notion sources")
    parser.add_argument("--registry", type=Path, default=ROOT / "projects.json")
    parser.add_argument("--window", default="auto",
                        help="auto rotates by UTC hour or a private cursor, or specify a window index")
    parser.add_argument("--window-state", type=Path,
                        help="optional private local round-robin cursor; only with --window auto")
    parser.add_argument("--skip-github-issues", action="store_true")
    parser.add_argument("--pin-issue", action="append", default=[],
                        help="bounded extra exact GitHub issue source PROJECT:NUMBER, max 3 in selected window")
    parser.add_argument("--job", action="append", default=[],
                        help="follow exact existing GitHub job PROJECT:RUN_ID:JOB_ID:FULL_HEAD_SHA; "
                             "selected window only; at most three; never retries jobs")
    parser.add_argument("--skip-ci-discovery", action="store_true")
    parser.add_argument("--read-notion-claims", action="store_true")
    parser.add_argument("--notion-data-source")
    parser.add_argument("--notion-claims-export", type=Path,
                        help="private saved connector active-status snapshot, not live ownership")
    parser.add_argument("--archive-target", type=Path,
                        help="optional existing external archive mount; metadata plan only")
    parser.add_argument("--archive-min-age-days", type=int, default=7)
    parser.add_argument("--disk-hotspots-root", type=Path)
    parser.add_argument("--disk-hotspots-output", type=Path)
    parser.add_argument("--disk-sqlite", action="append", default=[],
                        help="opt-in existing relative SQLite files; private metadata, max 8")
    parser.add_argument("--disk-inspect-unlinked-files", action="store_true",
                        help="opt-in private deleted-but-open files, bounded /proc, no cleanup")
    parser.add_argument("--disk-inspect-open-handles", action="store_true",
                        help="optional private bounded /proc fd metadata for disk candidates")
    parser.add_argument("--disk-hotspots-previous", type=Path,
                        help="private older JSON with same workspace and scanner parameters")
    parser.add_argument("--observe-vps-capacity", action="store_true")
    parser.add_argument("--observe-local-runner", action="store_true",
                        help="read-only bounded Linux /proc inspection of canonical zCloud listener/worker processes")
    parser.add_argument("--worker-events-database", type=Path,
                        help="optional existing history.db: indexed read-only worker progress, no worker claim/commands")
    parser.add_argument("--worker-previous", type=Path,
                        help="previous 0600 worker snapshot, compare indexed explicit progress vs heartbeat only")
    parser.add_argument("--inspect-unpr-branches",
                        help="canonical project ID in selected GitHub window for a bounded read-only branch ownership sample")
    parser.add_argument("--require-clear-unpr-slice", action="store_true",
                        help="fail closed if canonical un-PR source is incomplete or overlaps; never grants claims")
    parser.add_argument("--branch-prefix", help="optional exact branch prefix for source-limited branch inspection")
    parser.add_argument("--current-path", action="append", default=[],
                        help="proposed unclaimed repository-relative scope (only for --inspect-unpr-branches)")
    parser.add_argument("--inspect-pr", action="append", default=[],
                        help="read-only exact-head PR review/CI: PROJECT:NUMBER; at most three in selected window")
    parser.add_argument("--inspect-referenced-prs", action="store_true",
                        help="sample up to three existing PRs linked in the current P0/P1 product issue shortlist")
    parser.add_argument("--discover-open-prs", action="store_true",
                        help="read first 20 recently updated open PRs per selected project and prioritize existing work")
    parser.add_argument("--inspect-hosted-no-start", action="store_true",
                        help="opt-in: read at most one failed exact-head check annotation per selected PR; no actions")
    parser.add_argument("--state", type=Path, help="optional private ledger, never a live queue")
    parser.add_argument("--format", choices=("html", "json", "text", "ics", "desk", "handoff"), default="html")
    parser.add_argument("--show-owner-handoff", action="store_true",
                        help="opt-in P0-first owner strip; optionally pair with --owner-handoff-output")
    parser.add_argument("--owner-handoff-output", type=Path,
                        help="optional separate new private .csv owner-action export; requires --show-owner-handoff")
    parser.add_argument("--owner-handoff-json-output", type=Path,
                        help="matching .json owner handoff from the same source as optional .csv")
    parser.add_argument("--desk-output", type=Path,
                        help="new, create-only private .html output (required only for --format desk)")
    parser.add_argument("--output", type=Path,
                        help="new 0600 report: .html, .json or .md (text); never overwrites")
    parser.add_argument("--compare-with", type=Path, action="append",
                        help="repeat up to seven chronological previous portfolio JSON snapshots")
    args = parser.parse_args()
    window_lock = None
    ledger_lock = None
    try:
        if (args.disk_hotspots_root is None) != (args.disk_hotspots_output is None):
            raise ValueError("disk scan requires both root and private HTML output")
        if args.archive_target is not None and args.disk_hotspots_root is None:
            raise ValueError("--archive-target requires private disk scan")
        if args.disk_sqlite and args.disk_hotspots_root is None:
            raise ValueError("--disk-sqlite requires private disk scan with root/output")
        if args.disk_inspect_unlinked_files and args.disk_hotspots_root is None:
            raise ValueError("--disk-inspect-unlinked-files requires a private disk scan")
        if args.disk_inspect_open_handles and args.disk_hotspots_root is None:
            raise ValueError("--disk-inspect-open-handles requires a private disk scan")
        if args.disk_hotspots_previous is not None and args.disk_hotspots_root is None:
            raise ValueError("previous disk snapshot requires fresh private disk scan")
        if args.disk_hotspots_root is not None and args.format not in ("html", "json", "text"):
            raise ValueError("disk scan supports html, json, text only")
        if args.disk_hotspots_output is not None and args.disk_hotspots_output.suffix != ".html":
            raise ValueError("disk scan private output must end in .html")
        if args.github_only:
            if args.database is not None:
                raise ValueError("--github-only requires omitting --database")
            if args.observe_vps_capacity or args.observe_local_runner:
                raise ValueError("--github-only cannot infer local VPS runtime process or capacity evidence")
            if args.queue_previous is not None:
                raise ValueError("--queue-previous needs an actual read-only SQLite queue")
        elif args.database is None:
            raise ValueError("--database is required unless --github-only was chosen")
        if args.worker_previous is not None and args.worker_events_database is None:
            raise ValueError("--worker-previous requires --worker-events-database")
        if args.pin_issue and args.skip_github_issues:
            raise ValueError("--pin-issue requires actual GitHub issues source")
        if len(args.job) > 3:
            raise ValueError("--job accepts at most three exact existing job references")
        if args.inspect_hosted_no_start and not (
                args.inspect_pr or args.inspect_referenced_prs or args.discover_open_prs):
            raise ValueError("--inspect-hosted-no-start needs existing PR inspection")
        if args.format == "desk" and args.desk_output is None:
            raise ValueError("--format desk requires --desk-output")
        if args.show_owner_handoff and args.format not in (
                "html", "handoff", "json", "text"):
            raise ValueError("--show-owner-handoff requires html, handoff, json or text")
        if args.owner_handoff_output is not None:
            if not args.show_owner_handoff:
                raise ValueError("--owner-handoff-output requires --show-owner-handoff")
            if args.owner_handoff_output.suffix != ".csv":
                raise ValueError("--owner-handoff-output must be a new .csv file")
        if args.owner_handoff_json_output is not None:
            if not args.show_owner_handoff:
                raise ValueError("--owner-handoff-json-output requires --show-owner-handoff")
            if args.owner_handoff_json_output.suffix != ".json":
                raise ValueError("--owner-handoff-json-output must be a new .json file")
        if args.output is not None and args.format not in ("html", "json", "text", "handoff"):
            raise ValueError("--output supports only html, json, text or human handoff")
        if args.compare_with is not None and args.format not in ("html", "json", "text", "handoff"):
            raise ValueError("--compare-with supports html, json, text or owner handoff")
        history_files = args.compare_with or []
        if len(history_files) > 7 or len({p.resolve() for p in history_files}) != len(history_files):
            raise ValueError('at most seven distinct chronological previous reports')
        sources = [args.state, args.window_state, args.database,
                   args.worker_events_database, args.desk_output,
                   args.disk_hotspots_output, args.disk_hotspots_previous,
                   args.owner_handoff_output, args.owner_handoff_json_output]
        if any(p.resolve() == q.resolve() for p in history_files
               for q in sources if q is not None):
            raise ValueError('compare sources must differ from live state')
        owner_outputs = [args.owner_handoff_output, args.owner_handoff_json_output]
        for candidate in owner_outputs:
            if candidate is not None and any(
                    candidate.resolve() == p.resolve()
                    for p in [args.state, args.window_state, args.database,
                              args.worker_events_database, args.queue_previous,
                              args.worker_previous, args.notion_claims_export,
                              args.registry, args.output, args.desk_output,
                              args.disk_hotspots_output, args.disk_hotspots_previous,
                              *history_files, *(x for x in owner_outputs
                                                if x is not None and x is not candidate)]
                    if p is not None):
                raise ValueError("owner export conflicts with existing source/output")
        if args.disk_hotspots_previous is not None and any(
                args.disk_hotspots_previous.resolve() == p.resolve()
                for p in [args.state, args.window_state, args.database,
                          args.worker_events_database, args.desk_output,
                          args.output, *owner_outputs, *history_files] if p is not None):
            raise ValueError("previous disk snapshot overlaps a live source or output")
        if args.disk_hotspots_output is not None and any(
                args.disk_hotspots_output.resolve() == p.resolve()
                for p in [args.state, args.window_state, args.database,
                          args.worker_events_database, args.desk_output,
                          args.output, args.owner_handoff_output, *history_files] if p is not None):
            raise ValueError("disk report path overlaps private state or input")
        if args.output is not None and any(
                args.output.resolve() == v.resolve()
                for v in [*sources, *history_files] if v is not None):
            raise ValueError('private output must differ from input, state and ledger')
        if args.desk_output is not None:
            # Do not create a private HTML report on top of a pending
            # cursor or dedup ledger: a subsequent atomic JSON write could
            # otherwise clobber the report after its exclusive creation.
            private_inputs = [args.state] + [args.window_state]
            if any(item is not None and args.desk_output.resolve() == item.resolve()
                   for item in private_inputs):
                raise ValueError("desk-output must differ from private ledger/cursor paths")
        if args.state is not None:
            ledger_lock = acquire_private_ledger_lock(args.state)
        registry = canonical_projects(args.registry)
        windows = project_windows(registry)
        previous_window_state = None
        if args.state and args.window_state and args.state.resolve() == args.window_state.resolve():
            raise ValueError("window cursor and incident ledger must use separate private files")
        if args.window == "auto":
            if args.window_state:
                # Retain a nonblocking exclusive lock across ALL source reads
                # and the eventual cursor write. Cursor inode itself changes
                # atomically, so locking only that inode is not sufficient.
                window_lock = acquire_window_lock(args.window_state)
                window_index, previous_window_state, window_mode = select_next(
                    windows, registry, args.window_state)
            else:
                # Stateless compatibility: hourly rotation, no persistent cursor.
                window_index = int(datetime.now(timezone.utc).timestamp() // 3600) % len(windows)
                window_mode = "hourly_utc_rotation_no_scheduler"
        else:
            if args.window_state:
                raise ValueError("--window-state requires --window auto")
            if (not isinstance(args.window, str) or not args.window.isascii()
                    or not args.window.isdecimal()):
                raise ValueError("window must be auto or a nonnegative decimal index")
            window_index = int(args.window)
            window_mode = "explicit_manual_index"
        if not 0 <= window_index < len(windows):
            raise ValueError("window out of range for canonical projects")
        selected = windows[window_index]
        if args.read_notion_claims and args.notion_claims_export is not None:
            raise ValueError("choose one Notion observation mode")
        if (args.inspect_unpr_branches and args.inspect_unpr_branches not in selected):
            raise ValueError("branch inspection must target a project in the selected window")
        if (args.branch_prefix or args.current_path or args.require_clear_unpr_slice) and not args.inspect_unpr_branches:
            raise ValueError("branch scope/filter/strict mode require --inspect-unpr-branches")
        queue = (explicitly_unobserved_queue() if args.github_only
                 else observe(args.database))
        queue_churn = None
        queue_churn_error = None
        if args.queue_previous is not None:
            try:
                from zsentinel_disk_trend import load_previous
                from zsentinel_queue_churn import compare as compare_claim_attempts
                queue_churn = compare_claim_attempts(load_previous(args.queue_previous), queue)
            except (OSError, ValueError, json.JSONDecodeError):
                # This optional comparison never blocks GitHub/Notion/queue
                # observations. Do not invent absence of churn on failure.
                queue_churn_error = "private_previous_unavailable_or_source_mismatch"
        bundle, provenance = assemble(
            registry, selected, args.job, args.database,
            token=os.environ.get("GITHUB_TOKEN"),
            include_issues=not args.skip_github_issues,
            include_ci_discovery=not args.skip_ci_discovery,
            include_notion=args.read_notion_claims,
            notion_data_source=(args.notion_data_source or
                                os.environ.get("NOTION_QUEUE_DATA_SOURCE_ID")),
            notion_token=os.environ.get("NOTION_TOKEN"),
            notion_registry=args.registry,
            notion_export_path=args.notion_claims_export,
            observe_vps=args.observe_vps_capacity,
            queue_snapshot=queue,
            pinned_issues=args.pin_issue,
            worker_events_database=args.worker_events_database,
        )
        old = read_json(args.state, MAX_STATE_BYTES, "state") if (
            args.state and args.state.exists()) else None
        decision, state = plan(bundle, old)
        decision["live_sources"] = provenance
        if "worker_progress_observation" in bundle:
            decision["worker_progress_observation"] = bundle["worker_progress_observation"]
        if "notion_claims_observation" in bundle:
            decision["notion_claims_observation"] = bundle["notion_claims_observation"]
        # Complete all source validation and output rendering before
        # advancing the optional private dedup ledger. A failed HTML view or
        # corrupt source must not silently consume an incident notification.
        decision["private_ledger_written"] = bool(args.state)
        owner_handoff = None
        local_runner = None
        if args.observe_local_runner:
            from zsentinel_local_runner_observer import observe_local_runner
            local_runner = observe_local_runner()
            decision["local_runner_observation"] = local_runner
        worker_delta = None
        worker_delta_error = None
        if args.worker_previous is not None:
            try:
                from zsentinel_disk_trend import load_previous
                from zsentinel_worker_progress_delta import compare as compare_worker_progress
                previous_worker = load_previous(args.worker_previous)
                current_worker = bundle.get("worker_progress_observation")
                if current_worker is None:
                    raise ValueError("current worker progress source unavailable")
                worker_delta = compare_worker_progress(previous_worker, current_worker)
            except (OSError, ValueError, json.JSONDecodeError):
                # This optional feature does not block independent P0, CI,
                # issue or local capacity observation.
                worker_delta_error = "private_worker_source_unavailable_or_incomparable"
        if worker_delta is not None:
            from zsentinel_worker_progress_delta import public_summary as worker_delta_public
            decision["worker_progress_delta"] = worker_delta_public(worker_delta)
        elif worker_delta_error is not None:
            decision["worker_progress_delta"] = {
                "coverage": "unavailable", "reason": worker_delta_error,
                "product_delivery_verified": False,
                "claim_epoch_verified": False, "recovery_authorized": False}
        if queue_churn is not None:
            from zsentinel_queue_churn import public_summary as churn_public
            decision["queue_claim_churn"] = churn_public(queue_churn)
        elif queue_churn_error is not None:
            decision["queue_claim_churn"] = {
                "coverage": "unavailable", "reason": queue_churn_error,
                "automatic_retry_authorized": False,
                "lease_release_authorized": False, "claim_failure_proven": False}
        # Defer building the owner handoff until the optional existing-PR
        # desk has finished its already-authorized bounded read. Its exact
        # head / CI / review observations must share one source window with
        # current queue and incident actions, without another GitHub read.
        github_coverage = verified_source_coverage(selected, provenance, bundle)
        summary = portfolio_summary(registry, queue, selected, github_coverage, decision)
        runner_cross_source = None
        if local_runner is not None:
            from zsentinel_runner_cross_source import combine as combine_runner_evidence
            job_source = provenance.get("github_jobs") if isinstance(provenance, dict) else None
            fresh_jobs = job_source.get("refreshed_projects", []) if isinstance(job_source, dict) else []
            runner_cross_source = combine_runner_evidence(local_runner, fresh_jobs)
            decision["runner_cross_source"] = runner_cross_source
        attempts = coverage_history(
            windows, registry, previous_window_state if args.window_state else None,
            window_index, github_coverage)
        for row in summary["rows"]:
            attempt = attempts.get(row["project_id"])
            row["github_last_attempt_at_utc"] = attempt["at_utc"] if attempt else None
            row["github_last_attempt_coverage"] = attempt["coverage"] if attempt else None
            row["github_last_attempt_this_run"] = row["project_id"] in selected
            row["github_last_attempt_age_hours"] = (
                max(0, int((datetime.now(timezone.utc) -
                            datetime.fromisoformat(attempt["at_utc"])).total_seconds() // 3600))
                if attempt else None)
        branches = None
        if args.inspect_unpr_branches:
            from zsentinel_recent_branch_ownership import inventory
            branches = inventory(
                registry[args.inspect_unpr_branches], token=os.environ.get("GITHUB_TOKEN"),
                prefix=args.branch_prefix, current_paths=args.current_path)
            project_row = next(
                row for row in summary["rows"]
                if row["project_id"] == args.inspect_unpr_branches)
            if branches["potential_overlap_observed"]:
                project_row["next_action"] = "branch_overlap_needs_owner"
            elif branches["coverage"] != "bounded_complete":
                project_row["next_action"] = "branch_inventory_partial"
            elif not branches["current_paths_known"]:
                project_row["next_action"] = "branch_scope_not_provided"
            if (args.require_clear_unpr_slice
                    and not branches["recent_branch_slice_no_overlap_observed"]):
                # This asserts only no overlap in one verified branch slice.
                # Other claims and PR ownership remain independent gates.
                raise ValueError("canonical un-PR branch slice overlap or incomplete; no task claim")
        problem_inbox = next_problems(summary, decision, selected)
        # PR observation is explicitly opt-in. Never treat GitHub metadata
        # as an owner claim, required-check inventory or merge permission.
        if (args.inspect_pr or args.inspect_referenced_prs or args.discover_open_prs) and args.format in ("ics", "desk"):
            raise ValueError("existing PR desk supports only html, json or text")
        pr_selection = []
        for raw in args.inspect_pr:
            if not isinstance(raw, str):
                raise ValueError("invalid existing PR reference")
            match = re.fullmatch(r"([a-z][a-z0-9_-]{0,39}):([1-9][0-9]{0,8})", raw)
            if not match or match.group(1) not in selected:
                raise ValueError("--inspect-pr requires PROJECT:NUMBER in selected GitHub window")
            chosen = {"project_id": match.group(1), "pr_number": int(match.group(2))}
            if chosen in pr_selection or len(pr_selection) >= 3:
                raise ValueError("duplicate or over-capacity existing PR inspection")
            pr_selection.append(chosen)
        if args.inspect_referenced_prs:
            for item in problem_inbox["items"]:
                if len(pr_selection) >= 3:
                    break
                pid = item["project_id"]
                if pid not in selected:
                    continue
                for link in item["related_open_prs"]:
                    number = int(link.rsplit("/", 1)[-1])
                    candidate = {"project_id": pid, "pr_number": number}
                    if candidate not in pr_selection:
                        pr_selection.append(candidate)
                        if len(pr_selection) >= 3:
                            break
        pr_discovery = None
        if args.discover_open_prs and len(pr_selection) < 3:
            pr_discovery = discover_open_prs(
                registry, selected, token=os.environ.get("GITHUB_TOKEN"),
                limit=3-len(pr_selection), exclude=pr_selection)
            pr_selection.extend(pr_discovery["selection"])
        elif args.discover_open_prs:
            pr_discovery = {
                "schema": "zsentinel-open-pr-discovery-v1",
                "coverage": "not_requested_capacity_full", "selection": [],
                "calls": 0, "all_open_prs_observed": False,
                "owner_verified": False, "writes_performed": False,
                "merge_authorized": False}
        pr_desk = (observe_prs(
            registry, pr_selection, token=os.environ.get("GITHUB_TOKEN"),
            inspect_no_start_annotations=args.inspect_hosted_no_start)
                   if args.inspect_pr or args.inspect_referenced_prs or args.discover_open_prs
                   else None)
        if pr_desk is not None:
            pr_desk["open_pr_discovery"] = (pr_discovery if pr_discovery is not None else {
                "coverage": "not_requested", "all_open_prs_observed": False,
                "merge_authorized": False})
            # The same already-read exact-head PR advice is available in
            # HTML, JSON and plain text; no extra GitHub calls or claims.
            problem_inbox = link_existing_pr_actions(problem_inbox, pr_desk)
        if args.show_owner_handoff:
            from zsentinel_runtime_handoff import build_handoff
            owner_handoff = build_handoff(
                decision, registry=registry, queue_trend=queue_churn,
                pr_desk=pr_desk)
        unblock = blocker_first_board(summary, decision, problem_inbox, pr_desk=pr_desk)
        continuity = None
        routing_transition = None
        routing_history = None
        if history_files:
            from zsentinel_blocker_continuity import compare, compare_history
            from zsentinel_route_continuity import compare as routing_compare
            current = {'portfolio': summary, 'blocker_first_handoff': unblock,
                       'selected_projects': selected,
                       'source_bound_decision': decision}
            # Historical portfolio exports are owner-private. Reuse the
            # existing stable no-follow inode reader that checks owner/mode
            # on the opened file and rejects ambiguous duplicate JSON keys.
            snapshots = [
                read_json(path, 6 * 1024 * 1024, "previous private portfolio",
                          private_owner_only=True)
                for path in history_files
            ] + [current]
            continuity = (compare(snapshots[0], snapshots[1]) if len(snapshots) == 2
                          else compare_history(snapshots))
            # Reuse the already-loaded, latest chronological saved source.
            # A missing old router receipt stays UNKNOWN rather than healed.
            routing_transition = routing_compare(snapshots[-2], current)
            # Previously all but the last saved source were ignored for
            # task routing. Retain source-bound route movement across the
            # entire already-read window without further provider calls.
            if len(snapshots) > 2:
                from zsentinel_route_history import build_history
                routing_history = build_history(snapshots)
        disk_private_html = None
        if args.disk_hotspots_root is not None:
            from zsentinel_disk_hotspots import observe as disk_observe, html as disk_html, public_summary
            disk_report = disk_observe(args.disk_hotspots_root,
                min_mib=1 if args.archive_target is not None else 64)
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
        if args.format == "html":
            rendered = portfolio_html(summary, decision, window_index, windows,
                                      branch_inventory=branches, problem_inbox=problem_inbox,
                                      pr_desk=pr_desk, unblock=unblock, registry=registry)
            if owner_handoff is not None:
                from zsentinel_runtime_handoff import handoff_html
                if rendered.count("<main>") != 1:
                    raise ValueError("incompatible portfolio owner handoff HTML")
                rendered = rendered.replace("<main>", "<main>" +
                                            handoff_html(owner_handoff), 1)
            if worker_delta is not None:
                from zsentinel_worker_progress_delta import public_html as worker_delta_html
                if rendered.count("<main>") != 1:
                    raise ValueError("incompatible portfolio HTML for worker progress delta")
                rendered = rendered.replace("<main>", "<main>" +
                                            worker_delta_html(worker_delta), 1)
            if queue_churn is not None:
                from zsentinel_queue_churn import public_html as claim_churn_html
                if rendered.count("<main>") != 1:
                    raise ValueError("incompatible portfolio HTML for queue trend")
                rendered = rendered.replace("<main>", "<main>" +
                                            claim_churn_html(queue_churn), 1)
            if "disk_pressure_private_report" in decision:
                from zsentinel_disk_hotspots import public_summary_html
                if rendered.count("<main>") != 1:
                    raise ValueError("incompatible disk workbench portfolio HTML")
                rendered = rendered.replace("<main>", "<main>" +
                    public_summary_html(decision["disk_pressure_private_report"]), 1)
            if local_runner is not None:
                from zsentinel_local_runner_observer import runner_html
                if rendered.count("<main>") != 1:
                    raise ValueError("no canonical portfolio HTML main for runner evidence")
                rendered = rendered.replace("<main>", "<main>" +
                                            runner_html(local_runner), 1)
            if runner_cross_source is not None:
                from zsentinel_runner_cross_source import as_html as runner_cross_html
                if rendered.count("<main>") != 1:
                    raise ValueError("no canonical portfolio HTML main for runner correlation")
                rendered = rendered.replace("<main>", "<main>" +
                                            runner_cross_html(runner_cross_source), 1)
            if continuity is not None:
                from zsentinel_blocker_continuity import as_html
                fragment = as_html(continuity).split("<main>", 1)[1].split("</main>", 1)[0]
                if rendered.count("<main>") != 1:
                    raise ValueError("incompatible portfolio HTML")
                rendered = rendered.replace("<main>", "<main><section aria-label=\"Broncontinuïteit\">" +
                                            fragment + "</section>", 1)
            if routing_transition is not None:
                from zsentinel_route_continuity import as_html as routing_transition_html
                if rendered.count("<main>") != 1:
                    raise ValueError("incompatible portfolio HTML for route transitions")
                rendered = rendered.replace(
                    "<main>", "<main>" + routing_transition_html(routing_transition), 1)
            if routing_history is not None:
                from zsentinel_route_history import owner_html as route_history_html
                if rendered.count("<main>") != 1:
                    raise ValueError("incompatible portfolio HTML for route history")
                rendered = rendered.replace(
                    "<main>", "<main>" + route_history_html(routing_history), 1)
        elif args.format == "ics":
            from zsentinel_human_agenda import human_calendar
            rendered = human_calendar(decision)
        elif args.format == "desk":
            from zsentinel_incident_desk import review_html
            rendered = review_html(decision)
        elif args.format == "handoff":
            from zsentinel_unblock_board import human_handoff_markdown
            rendered = human_handoff_markdown(unblock)
            if owner_handoff is not None:
                from zsentinel_runtime_handoff import markdown as owner_markdown
                rendered = owner_markdown(owner_handoff) + "\n\n" + rendered
            if worker_delta is not None:
                from zsentinel_worker_progress_delta import human_text as worker_delta_text
                rendered = ("## zSentinel · worker heartbeat versus progress #1342\n\n" +
                            worker_delta_text(worker_delta) + "\n\n" + rendered)
            elif worker_delta_error is not None:
                rendered = ("## zSentinel · workerbron-onzekerheid\n\n"
                            "Bronmomenten niet vergelijkbaar; geen herstelbewijs.\n\n" + rendered)
            if queue_churn is not None:
                from zsentinel_queue_churn import human_text as queue_churn_text
                rendered = ("## zSentinel · claimpogingen/lease-eigenaar #677\n\n" +
                            queue_churn_text(queue_churn) + "\n\n" + rendered)
            elif queue_churn_error is not None:
                rendered = ("## zSentinel · queue-vergelijking onzeker\n\n"
                            "Oude bron geweigerd; retries of afwezigheid niet bewezen.\n\n" +
                            rendered)
            if runner_cross_source is not None:
                from zsentinel_runner_cross_source import as_text as runner_cross_text
                rendered = ("## zSentinel · exacte runner en lokale VPS-observatie\n\n" +
                            runner_cross_text(runner_cross_source) + "\n\n" + rendered)
            if continuity is not None:
                from zsentinel_blocker_continuity import as_text as blocker_history_text
                rendered = ("## zSentinel · continuïteit van blokkades\n\n" +
                            blocker_history_text(continuity) + "\n\n" + rendered)
            if routing_transition is not None:
                from zsentinel_route_continuity import as_text as routing_history_text
                rendered = ("## zSentinel · veranderingen in werkroutering\n\n" +
                            routing_history_text(routing_transition) + "\n\n" + rendered)
            if routing_history is not None:
                from zsentinel_route_history import owner_text as route_history_text
                rendered = ("## zSentinel · herstelgeschiedenis over alle bronvensters\n\n" +
                            route_history_text(routing_history) + "\n\n" + rendered)
        elif args.format == "json":
            rendered = json.dumps({
                "portfolio": summary, "selected_window": window_index,
                "selection_mode": window_mode,
                "window_count": len(windows), "selected_projects": selected,
                "source_bound_decision": decision,
                "next_problem_shortlist": problem_inbox,
                "blocker_first_handoff": unblock,
                "blocker_continuity": continuity,
                "routing_transition": routing_transition,
                "routing_history": routing_history,
                "local_runner_observation": local_runner,
                "runner_cross_source": runner_cross_source,
                "queue_claim_churn": decision.get("queue_claim_churn"),
                "owner_handoff": owner_handoff,
                "worker_progress_delta": decision.get("worker_progress_delta"),
                "existing_pr_delivery": (pr_desk if pr_desk is not None else
                    {"coverage": "not_requested", "merge_authorized": False}),
                "recent_unpr_branch_ownership": (
                    branches if branches is not None else
                    {"coverage": "not_requested", "owner_verified": False}),
            }, ensure_ascii=True, sort_keys=True, indent=2)
        else:
            lines = ["zSentinel portfolio | venster %d/%d | GitHub: %s" % (
                window_index + 1, len(windows), ", ".join(selected))]
            lines.append("Portfoliobreed: GitHub buiten dit venster NIET GESCAND. Geen herstelbewijs.")
            notion_windows = summary.get("notion_status_source_windows")
            if notion_windows is None:
                lines.append("Notion statusvensters: ONBEKEND; geen afzonderlijk live bronbewijs.")
            else:
                for item in notion_windows:
                    lines.append(
                        "Notion %s: ≥%s werkregels uit %s/%s pagina's · %s; "
                        "geen claim/lease/vrijgavebewijs." % (
                            item["status"], item["observed_rows"],
                            item["pages_read"], item["page_budget"],
                            "ONVOLLEDIG: bron herlezen door eigenaar"
                            if item["coverage"] == "partial"
                            else "binnen venster gelezen"))
            if runner_cross_source is not None:
                from zsentinel_runner_cross_source import as_text as runner_cross_text
                lines.append(runner_cross_text(runner_cross_source))
            if worker_delta is not None:
                from zsentinel_worker_progress_delta import human_text as worker_delta_text
                lines.append(worker_delta_text(worker_delta))
            elif worker_delta_error is not None:
                lines.append("WORKER #1342: vorige bron ontbreekt of is niet vergelijkbaar.")
            if queue_churn is not None:
                from zsentinel_queue_churn import human_text as queue_churn_text
                lines.append(queue_churn_text(queue_churn))
            elif queue_churn_error is not None:
                lines.append("QUEUE #677: vorige privébron niet leesbaar of niet vergelijkbaar; status onbekend.")
            if args.github_only:
                lines.append("QUEUE: NIET WAARGENOMEN (--github-only); geen nulmeting, vrijgave of leasebewijs.")
            if provenance.get("pinned_requested"):
                lines.append(pinned_receipts_text(provenance))
            if provenance.get("exact_jobs_pinned_requested"):
                lines.append("Exacte bestaande GitHub-jobs expliciet gekozen: %s; "
                             "alleen gelezen, geen rerun, claim of runnerherstel."
                             % provenance["exact_jobs_pinned_requested"])
            lines.append(blocker_board_text(unblock))
            if local_runner is not None:
                from zsentinel_local_runner_observer import runner_text
                lines.append(runner_text(local_runner))
            if "worker_progress_observation" in decision:
                lines.append(worker_progress_text(decision["worker_progress_observation"]))
            elif args.worker_events_database is not None:
                lines.append("WORKER-EVENTBRON ONBESCHIKBAAR · geen bewijs van workerhervatting.")
            lines.append("VOLGENDE PROBLEMEN (advies; eigendom en scope NIET geverifieerd):")
            for item in problem_inbox["items"]:
                lines.append("  %s %s %s | %s | %s" % (
                    item["priority"], item["project_id"], item["issue_url"],
                    item["next_step"], item["action"]))
                for linked in item.get("inspected_linked_prs", []):
                    lines.append(
                        "    bestaande PR %s | source=%s | head=%s | %s | "
                        "issuefix NIET bewezen; merge/claim NIET toegestaan" % (
                            linked["pr_url"], linked["coverage"],
                            linked["observed_head_sha"][:12] if
                            linked["observed_head_sha"] else "ONBEKEND",
                            linked["next_action"]))
            if not problem_inbox["items"]:
                lines.append("  Geen kandidaten in beperkte steekproef; GEEN bewijs van nul open issues.")
            if pr_desk is not None:
                lines.append("BESTAANDE PR'S (exact-head steekproef, GEEN mergetoestemming):")
                if pr_discovery is not None:
                    lines.append("  Open PR bron: maximaal 20 recent bijgewerkte PRs/project; eigenaar en compleetheid ONBEKEND.")
                for item in pr_desk["items"]:
                    lines.append("  %s PR #%s head=%s | bron=%s | vervolg=%s | %s" % (
                        item["project_id"], item["pr_number"],
                        item.get("head_sha", "ONBEKEND")[:12],
                        item["coverage"], item["next_step"], item["url"]))
                if not pr_desk["items"]:
                    lines.append("  Geen PR-referenties in beperkte selectie; GEEN bewijs van nul PR's.")
            for item in problem_inbox["owner_only_incidents"]:
                lines.append("  EIGENAAR NODIG %s %s: %s" % (
                    item["severity"], item["project_id"], item["action"]))
            historic = decision["historical_unresolved"]
            lines.append("Historische incidenten uit privé-ledger: %s open, %s weergegeven; "
                         "GEEN geverifieerd herstel of nieuwe autorisatie." % (
                             historic["total_unresolved"], len(historic["shown"])))
            if historic["truncated"]:
                lines.append("Historische lijst afgekapt, rest NIET opgelost.")
            for row in summary["rows"]:
                lines.append("%s | queued=%s | owned=%s | notion>=%s | churn=%s | lease=%s | lease_onbekend=%s | claimvenster_inconsistent=%s | P0-P1>=%s | P2-onbekend>=%s | incidents>=%s | Github=%s | route=%s | action=%s" % (
                    row["project_id"], _count(row["queued"]), _count(row["owned"]),
                    _count(row["notion_active_status_min"]),
                    _count(row["high_claim_attempts"]), _count(row["expired_owner_leases"]),
                     _count(row["unverifiable_owner_leases"]),
                    _count(row["inconsistent_owner_lease_windows"]),
                    _count(row["p0_p1_issue_sample_hits"]),
                    _count(row["p2_unranked_issue_sample_hits"]),
                    _count(row["incident_sample_hits"]),
                    row["github_observation"], row["github_job_runner_route"],
                    row["next_action"]))
                if row["notion_verifying_status_min"] is not None:
                    lines.append("  Notion Verifying ≥%s · eigenaar hercontroleren; geen live leasebewijs." % (
                        row["notion_verifying_status_min"],))
                lines.append("  GitHub jobs: %s; wachtend self-hosted ≥%s; aanvullende joblijst afgekapt=%s. "
                             "Steekproef, geen volledig CI- of runnergezondheidsbewijs." % (
                    _count(row["github_sampled_actionable_jobs"]),
                    _count(row["github_sampled_queued_self_hosted_jobs_min"]),
                    row["github_other_jobs_list_truncated"]))
                lines.append("  GitHub bronpoging: %s (%s, huidige run: %s); historische poging is GEEN health." % (
                    row["github_last_attempt_at_utc"] or "ONBEKEND",
                    row["github_last_attempt_coverage"] or "ONBEKEND",
                    row["github_last_attempt_this_run"]))
                lines.append("  Historisch NIET gereconcilieerd: %s in max.30-ledgersample; "
                             "is GEEN huidig GitHub-incident of herstelbewijs." % (
                                 row["historical_unresolved_sample_hits"],))
            if branches is not None:
                lines.append("BRANCH EIGENDOM (%s): %s; recente kandidaten=%s; "
                             "mogelijke pad-overlap=%s; GEEN eigendomsbevestiging." % (
                                 branches["repository"], branches["coverage"],
                                 branches["recent_candidate_count"],
                                 str(branches["potential_overlap_observed"])))
                for entry in branches["recent_candidates"]:
                    lines.append("  %s @ %s age=%sh files=%s overlap=%s" % (
                        entry["branch"], entry["head_sha"][:12],
                        entry["commit_age_hours"], entry["path_count"],
                        str(entry["potential_overlap"])))
            lines.append("Geen actieve claim of runtime gemuteerd; eigenaarstatus moet apart worden geverifieerd.")
            if "disk_pressure_private_report" in decision:
                disk = decision["disk_pressure_private_report"]
                lines.append("SCHIJFRUIMTE: privé HTML; begrensd >=%s GiB"
                             " en >=%s grote bestanden, dekking=%s;"
                             " geen verwijdering of workeractie." % (
                    disk["observed_allocated_gib_min"],
                    disk["large_file_candidates_min"], disk["coverage"]))
            rendered = "\n".join(lines)
            if owner_handoff is not None:
                from zsentinel_runtime_handoff import markdown as owner_markdown
                rendered = owner_markdown(owner_handoff) + "\n\n" + rendered
            if continuity is not None:
                from zsentinel_blocker_continuity import as_text
                rendered = as_text(continuity) + "\n" + rendered
            if routing_transition is not None:
                from zsentinel_route_continuity import as_text as routing_transition_text
                rendered = routing_transition_text(routing_transition) + "\n\n" + rendered
            if routing_history is not None:
                from zsentinel_route_history import owner_text as route_history_text
                rendered = route_history_text(routing_history) + "\n\n" + rendered
        if disk_private_html is not None:
            from zsentinel_private_output import write_private_output
            write_private_output(args.disk_hotspots_output, disk_private_html, "html")
        if args.output is not None:
            from zsentinel_private_output import write_private_output
            write_private_output(
                args.output, rendered,
                "md" if args.format in ("text", "handoff") else args.format)
        if any(output is not None for output in owner_outputs):
            if owner_handoff is None:
                raise ValueError("owner exports need the original owner handoff")
            from zsentinel_private_output import write_private_output
            from zsentinel_runtime_handoff import serialize as serialize_owner_handoff
            # Same in-memory report for both: no additional provider read.
            if args.owner_handoff_json_output is not None:
                write_private_output(
                    args.owner_handoff_json_output,
                    serialize_owner_handoff(owner_handoff, ".json"), "json")
            if args.owner_handoff_output is not None:
                write_private_output(
                    args.owner_handoff_output,
                    serialize_owner_handoff(owner_handoff, ".csv"), "csv")
        if args.desk_output is not None:
            from zsentinel_incident_desk import write_private_html, review_html
            private_desk = rendered if args.format == "desk" else review_html(decision)
            write_private_html(args.desk_output, private_desk)
        if args.window_state:
            # A concurrent run must never overwrite a completed scan cursor.
            advance(args.window_state, windows, registry, previous_window_state,
                    window_index, github_coverage)
        if args.state:
            write_json_atomic(args.state, state)
        if args.format != "desk" and args.output is None:
            print(rendered)
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as exc:
        parser.exit(2, f"zSentinel portfolio overview unavailable: {exc}\n")
    finally:
        if window_lock is not None:
            window_lock.close()
        if ledger_lock is not None:
            ledger_lock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

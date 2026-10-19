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
import sqlite3
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from urllib.parse import quote

from zsentinel_product_issue_inbox import canonical_projects
from zsentinel_queue_observer import observe
from zsentinel_live_cockpit import assemble, ROOT, MAX_PROJECTS
from zsentinel_recovery_router import MAX_STATE_BYTES, plan, read_json, write_json_atomic
from zsentinel_recovery_view import html_view


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
        ci_ok = (len(ci_status) == 1 and ci_status[0] in (
            "selected_one_source_bound_job", "no_actionable_job_in_sampled_windows"))
        issue_ok = (len(issue_status) == 1 and issue_status[0] == "partial"
                    and not inbox.get("stopped_after_rate_or_auth_denial", False))
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
    if (not isinstance(queue, dict)
            or queue.get("schema") != "zsentinel-queue-pressure-v1"
            or queue.get("database_read_only") is not True
            or queue.get("claim_ownership_verified") is not False):
        raise ValueError("untrusted queue observation")
    totals = queue.get("totals")
    groups = queue.get("project_groups")
    if not isinstance(totals, dict) or not isinstance(groups, list):
        raise ValueError("incomplete queue pressure contract")
    complete = queue.get("queue_inventory_complete") is True
    if (not isinstance(github_coverage, dict) or set(github_coverage) != set(selected)):
        raise ValueError("GitHub source coverage missing for selected window")
    triage = decision.get("open_product_issue_triage", {})
    incidents = decision.get("incidents", [])
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
            "queue_observation": ("complete_active_rows" if complete else "partial_unreliable"),
            "queued": 0 if complete else None,
            "owned": 0 if complete else None,
            "high_claim_attempts": 0 if complete else None,
            "expired_owner_leases": 0 if complete else None,
            "worker_ownership_verified": False,
            "recovery_proven": False, "next_action": "read_only_inspect_current_source",
            "p0_p1_issue_sample_hits": None, "incident_sample_hits": None,
            "notion_active_status_min": None,
            "github_job_runner_route": "not_scanned",
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
    # Counts below are lower bounds in the bounded source window,
    # NEVER total open portfolio issue/incident inventory.
    for pid in selected:
        if github_coverage[pid] in ("partial_both_sources", "issue_sample_only"):
            summary[pid]["p0_p1_issue_sample_hits"] = sum(
                isinstance(item, dict) and item.get("project_id") == pid
                for item in issue_items
            )
        if github_coverage[pid] in ("partial_both_sources", "ci_sample_only"):
            summary[pid]["incident_sample_hits"] = sum(
                isinstance(item, dict) and item.get("project_id") == pid
                for item in incidents
            )
    notion = decision.get("notion_claims_observation")
    if notion is not None:
        if (not isinstance(notion, dict)
                or notion.get("source") != "notion_readonly_data_source_pages"
                or notion.get("claim_scope_verified") is not False
                or notion.get("can_dispatch") is not False
                or notion.get("coverage") not in ("current_pages_only", "incomplete")
                or not isinstance(notion.get("active_project_counts"), dict)):
            raise ValueError("invalid Notion project statuses")
        counts = notion["active_project_counts"]
        for key, value in counts.items():
            if not isinstance(key, str) or type(value) is not int or value < 0 or value > 400:
                raise ValueError("invalid active project status counter")
        for pid in summary:
            summary[pid]["notion_active_status_min"] = counts.get(pid, 0)
    unassigned_hosted_projects = {
        item.get("project_id") for item in incidents
        if isinstance(item, dict) and item.get("problem") == "github_job_unassigned"
    }
    for row in summary.values():
        if row["project_id"] in unassigned_hosted_projects:
            row["next_action"] = "selfhosted_unassigned_owner_review"
        elif not complete:
            row["next_action"] = "queue_source_incomplete_owner_must_review"
        elif row["expired_owner_leases"]:
            row["next_action"] = "request_owner_lease_review_never_release"
        elif row["high_claim_attempts"]:
            row["next_action"] = "inspect_claim_churn_no_auto_retry"
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
        "queue_owner_authorized": False,
        "unregistered_queue_project_groups": unknown_count,
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


def portfolio_html(summary, decision, window_index, windows, branch_inventory=None):
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
            "selfhosted_unassigned_owner_review": "P0: GitHub runnerlabels, repo-toegang en listener door eigenaar controleren",
            "request_owner_lease_review_never_release": "Vraag leasebeoordeling; niet vrijgeven",
            "inspect_claim_churn_no_auto_retry": "Onderzoek claimchurn; geen retry",
            "github_source_not_scanned_next_window": "GitHub onbekend; volgend venster",
            "github_source_unavailable_no_all_clear": "Bronfout: opnieuw via geautoriseerde route",
            "branch_overlap_needs_owner": "RECENTE BRANCHOVERLAP: eigenaar + PR-scope controleren",
            "branch_inventory_partial": "PR-loze branchbron onvolledig: geen vrijgave",
            "branch_scope_not_provided": "Geen actuele padscope: overlap onbekend",
            "inspect_open_issue_pr_and_exact_ci_gates": "Bekijk issues, PR en exacte CI",
        }.get(row["next_action"])
        if action_text is None:
            raise ValueError("unsupported portfolio recommendation")
        cells.append(
            '<tr><th scope="row"><a href="' + link +
            '" target="_blank" rel="noopener noreferrer">' + name + '</a></th>'
            '<td>' + _count(row["queued"]) + '</td>'
            '<td>' + _count(row["owned"]) + '</td>'
            '<td>' + ('ONBEKEND' if row["notion_active_status_min"] is None
                      else '≥' + str(row["notion_active_status_min"])) + '</td>'
            '<td>' + _count(row["high_claim_attempts"]) + '</td>'
            '<td>' + _count(row["expired_owner_leases"]) + '</td>'
            '<td>' + escape(github) + '</td>'
            '<td>' + phrase_route(row["github_job_runner_route"]) + '</td>'
            '<td>' + ('ONBEKEND' if row["p0_p1_issue_sample_hits"] is None
                      else '≥' + str(row["p0_p1_issue_sample_hits"])) + '</td>'
            '<td>' + ('ONBEKEND' if row["incident_sample_hits"] is None
                      else '≥' + str(row["incident_sample_hits"])) + '</td>'
            '<td>' + escape(action_text) + '</td></tr>'
        )
    selected = ", ".join(escape(x) for x in windows[window_index])
    caption = (
        f'Venster {window_index + 1} van {len(windows)}: <strong>{selected}</strong>. '
        f'{summary["canonical_projects"]} canonieke projecten zichtbaar; '
        f'{summary["github_projects_observed"]} van '
        f'{summary["github_projects_selected"]} geselecteerde projecten met '
        'daadwerkelijk beperkte GitHub-bronobservatie. '
        'Niet opgevraagd of mislukt betekent niet gezond.'
    )
    coverage = (
        "Huidige wachtrijmomentopname is volledig binnen de observerlimiet; "
        "maar er is GEEN geverifieerd bestandseigendom."
        if summary["queue_inventory_complete"] else
        "Wachtrijbron onvolledig: cijfers bewust ONBEKEND, geen nul-melding."
    )
    table = (
        '<section><h2>Volledig portfolio · gedeeltelijke broncontrole</h2>'
        '<p class="muted">' + caption + '</p><p class="banner">' + coverage +
        ' Geen claim, workflow, herstart, lease of deploy uitgevoerd.</p>'
        '<div class="wrap"><table><thead><tr><th scope="col">Project</th>'
        '<th scope="col">Wachtend</th><th scope="col">Bezet</th>'
        '<th scope="col">Notion actief (min.)</th>'
        '<th scope="col">Veel claims</th><th scope="col">Lease verlopen?</th>'
        '<th scope="col">GitHub-bereik</th><th scope="col">Runnerroute</th>'
        '<th scope="col">P0/P1-issues (min.)</th>'
        '<th scope="col">Incidenten (min.)</th><th scope="col">Veilige vervolgstap</th>'
        '</tr></thead><tbody>' +
        "".join(cells) + '</tbody></table></div>'
        '<p class="muted small">Standaard kiest <code>--window auto</code> '
        'elk UTC-uur een ander bronvenster, alleen bij een nieuwe '
        'handmatig of geautoriseerd geplande uitvoering. '
        'Een getal ≥0 is alleen een ondergrens van de steekproef, '
        'nooit het totale issue- of incidentenaantal. '
        'Handmatig kiezen: <code>--window 0</code> tot <code>--window ' +
        str(len(windows)-1) +
        '</code>. Eén run scant nooit stiekem alle repositories via GitHub.</p>'
        '</section>'
    )
    raw = html_view(decision)
    if raw.count("<main>") != 1:
        raise ValueError("incompatible underlying offline operator view")
    return raw.replace("<main>", "<main>" + table + branch_ownership_html(branch_inventory), 1)


def main():
    parser = argparse.ArgumentParser(description="Read-only complete zSentinel portfolio and bounded GitHub window")
    parser.add_argument("--database", type=Path, required=True,
                        help="existing zCloud SQLite file (never created or modified)")
    parser.add_argument("--registry", type=Path, default=ROOT / "projects.json")
    parser.add_argument("--window", default="auto",
                        help="auto rotates by UTC hour, or specify zero-based window index")
    parser.add_argument("--skip-github-issues", action="store_true")
    parser.add_argument("--skip-ci-discovery", action="store_true")
    parser.add_argument("--read-notion-claims", action="store_true")
    parser.add_argument("--notion-data-source")
    parser.add_argument("--observe-vps-capacity", action="store_true")
    parser.add_argument("--inspect-unpr-branches",
                        help="canonical project ID in selected GitHub window for a bounded read-only branch ownership sample")
    parser.add_argument("--require-clear-unpr-slice", action="store_true",
                        help="fail closed if canonical un-PR source is incomplete or overlaps; never grants claims")
    parser.add_argument("--branch-prefix", help="optional exact branch prefix for source-limited branch inspection")
    parser.add_argument("--current-path", action="append", default=[],
                        help="proposed unclaimed repository-relative scope (only for --inspect-unpr-branches)")
    parser.add_argument("--state", type=Path, help="optional private ledger, never a live queue")
    parser.add_argument("--format", choices=("html", "json", "text"), default="html")
    args = parser.parse_args()
    try:
        registry = canonical_projects(args.registry)
        windows = project_windows(registry)
        if args.window == "auto":
            # All fixed-hour recurring invocations rotate windows, without a
            # disk cursor, extra GitHub requests or a long-running scheduler.
            window_index = int(datetime.now(timezone.utc).timestamp() // 3600) % len(windows)
            window_mode = "hourly_utc_rotation_no_scheduler"
        else:
            if (not isinstance(args.window, str) or not args.window.isascii()
                    or not args.window.isdecimal()):
                raise ValueError("window must be auto or a nonnegative decimal index")
            window_index = int(args.window)
            window_mode = "explicit_manual_index"
        if not 0 <= window_index < len(windows):
            raise ValueError("window out of range for canonical projects")
        selected = windows[window_index]
        if (args.inspect_unpr_branches and args.inspect_unpr_branches not in selected):
            raise ValueError("branch inspection must target a project in the selected window")
        if (args.branch_prefix or args.current_path or args.require_clear_unpr_slice) and not args.inspect_unpr_branches:
            raise ValueError("branch scope/filter/strict mode require --inspect-unpr-branches")
        queue = observe(args.database)
        bundle, provenance = assemble(
            registry, selected, [], args.database,
            token=os.environ.get("GITHUB_TOKEN"),
            include_issues=not args.skip_github_issues,
            include_ci_discovery=not args.skip_ci_discovery,
            include_notion=args.read_notion_claims,
            notion_data_source=(args.notion_data_source or
                                os.environ.get("NOTION_QUEUE_DATA_SOURCE_ID")),
            notion_token=os.environ.get("NOTION_TOKEN"),
            notion_registry=args.registry,
            observe_vps=args.observe_vps_capacity,
            queue_snapshot=queue,
        )
        old = read_json(args.state, MAX_STATE_BYTES, "state") if (
            args.state and args.state.exists()) else None
        decision, state = plan(bundle, old)
        decision["live_sources"] = provenance
        if "notion_claims_observation" in bundle:
            decision["notion_claims_observation"] = bundle["notion_claims_observation"]
        # Complete all source validation and output rendering before
        # advancing the optional private dedup ledger. A failed HTML view or
        # corrupt source must not silently consume an incident notification.
        decision["private_ledger_written"] = bool(args.state)
        github_coverage = verified_source_coverage(selected, provenance, bundle)
        summary = portfolio_summary(registry, queue, selected, github_coverage, decision)
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
        if args.format == "html":
            rendered = portfolio_html(summary, decision, window_index, windows,
                                      branch_inventory=branches)
        elif args.format == "json":
            rendered = json.dumps({
                "portfolio": summary, "selected_window": window_index,
                "selection_mode": window_mode,
                "window_count": len(windows), "selected_projects": selected,
                "source_bound_decision": decision,
                "recent_unpr_branch_ownership": (
                    branches if branches is not None else
                    {"coverage": "not_requested", "owner_verified": False}),
            }, ensure_ascii=True, sort_keys=True, indent=2)
        else:
            lines = ["zSentinel portfolio | venster %d/%d | GitHub: %s" % (
                window_index + 1, len(windows), ", ".join(selected))]
            lines.append("Portfoliobreed: GitHub buiten dit venster NIET GESCAND. Geen herstelbewijs.")
            for row in summary["rows"]:
                lines.append("%s | queued=%s | owned=%s | notion>=%s | churn=%s | lease=%s | issues>=%s | incidents>=%s | Github=%s | route=%s | action=%s" % (
                    row["project_id"], _count(row["queued"]), _count(row["owned"]),
                    _count(row["notion_active_status_min"]),
                    _count(row["high_claim_attempts"]), _count(row["expired_owner_leases"]),
                    _count(row["p0_p1_issue_sample_hits"]), _count(row["incident_sample_hits"]),
                    row["github_observation"], row["github_job_runner_route"],
                    row["next_action"]))
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
            rendered = "\n".join(lines)
        if args.state:
            write_json_atomic(args.state, state)
        print(rendered)
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as exc:
        parser.exit(2, f"zSentinel portfolio overview unavailable: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

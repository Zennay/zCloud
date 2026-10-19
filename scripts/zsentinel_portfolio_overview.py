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
    for row in summary.values():
        if not complete:
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


def portfolio_html(summary, decision, window_index, windows):
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
            "request_owner_lease_review_never_release": "Vraag leasebeoordeling; niet vrijgeven",
            "inspect_claim_churn_no_auto_retry": "Onderzoek claimchurn; geen retry",
            "github_source_not_scanned_next_window": "GitHub onbekend; volgend venster",
            "github_source_unavailable_no_all_clear": "Bronfout: opnieuw via geautoriseerde route",
            "inspect_open_issue_pr_and_exact_ci_gates": "Bekijk issues, PR en exacte CI",
        }.get(row["next_action"])
        if action_text is None:
            raise ValueError("unsupported portfolio recommendation")
        cells.append(
            '<tr><th scope="row"><a href="' + link +
            '" target="_blank" rel="noopener noreferrer">' + name + '</a></th>'
            '<td>' + _count(row["queued"]) + '</td>'
            '<td>' + _count(row["owned"]) + '</td>'
            '<td>' + _count(row["high_claim_attempts"]) + '</td>'
            '<td>' + _count(row["expired_owner_leases"]) + '</td>'
            '<td>' + escape(github) + '</td>'
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
        '<th scope="col">Veel claims</th><th scope="col">Lease verlopen?</th>'
        '<th scope="col">GitHub-bereik</th><th scope="col">P0/P1-issues (min.)</th>'
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
    return raw.replace("<main>", "<main>" + table, 1)


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
        if args.state:
            write_json_atomic(args.state, state)
        decision["private_ledger_written"] = bool(args.state)
        github_coverage = verified_source_coverage(selected, provenance, bundle)
        summary = portfolio_summary(registry, queue, selected, github_coverage, decision)
        if args.format == "html":
            print(portfolio_html(summary, decision, window_index, windows))
        elif args.format == "json":
            print(json.dumps({
                "portfolio": summary, "selected_window": window_index,
                "selection_mode": window_mode,
                "window_count": len(windows), "selected_projects": selected,
                "source_bound_decision": decision,
            }, ensure_ascii=True, sort_keys=True, indent=2))
        else:
            print("zSentinel portfolio | venster %d/%d | GitHub: %s" % (
                window_index + 1, len(windows), ", ".join(selected)))
            print("Portfoliobreed: GitHub buiten dit venster NIET GESCAND. Geen herstelbewijs.")
            for row in summary["rows"]:
                print("%s | queued=%s | owned=%s | churn=%s | lease=%s | issues>=%s | incidents>=%s | Github=%s" % (
                    row["project_id"], _count(row["queued"]), _count(row["owned"]),
                    _count(row["high_claim_attempts"]), _count(row["expired_owner_leases"]),
                    _count(row["p0_p1_issue_sample_hits"]), _count(row["incident_sample_hits"]),
                    row["github_observation"]))
            print("Geen actieve claim of runtime gemuteerd; eigenaarstatus moet apart worden geverifieerd.")
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as exc:
        parser.exit(2, f"zSentinel portfolio overview unavailable: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

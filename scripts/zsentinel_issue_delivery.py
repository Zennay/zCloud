#!/usr/bin/env python3
"""One-shot, read-only zSentinel issue-to-PR product delivery workboard.

Consumes the canonical GitHub issue inbox and existing exact-head PR reader.
No queue, Notion, runner, file ownership, CI retry, merge or deploy mutation.
"""
from __future__ import annotations

import argparse
import json
import os
from html import escape
from pathlib import Path

from zsentinel_product_issue_inbox import ROOT, canonical_projects, collect, observed_time
from zsentinel_pr_delivery import NEXT_ACTION_NL, observe_prs
from zsentinel_issue_progress import load_private_prior, progress_since

MAX_PROJECTS = 3
MAX_ISSUES = 24
MAX_PRS = 3
SEVERITY = ("P0", "P1", "P2", "Unspecified")
HUMAN_ONLY = frozenset({
    "https://github.com/Zennay/zCloud/issues/1326",
    "https://github.com/Zennay/zCloud/issues/1523",
})


def build_workboard(inbox, registry, token=None):
    """Select previously referenced PRs first, never mint owner authority."""
    if (not isinstance(inbox, dict)
            or inbox.get("schema") != "zsentinel-product-issue-inbox-v1"
            or inbox.get("actions_authorized") is not False
            or inbox.get("active_claims_verified") is not False
            or not isinstance(inbox.get("items"), list)
            or len(inbox["items"]) > MAX_ISSUES
            or not isinstance(inbox.get("projects"), list)
            or not 1 <= len(inbox["projects"]) <= MAX_PROJECTS
            or not isinstance(inbox.get("coverage"), list)
            or len(inbox["coverage"]) != len(inbox["projects"])):
        raise ValueError("untrusted or unbounded issue evidence")
    project_names = inbox["projects"]
    if len(set(project_names)) != len(project_names):
        raise ValueError("duplicate issue sources")
    if (not isinstance(registry, dict)
            or any(registry.get(name) is None for name in project_names)):
        raise ValueError("unknown canonical project source")
    sampled = []
    prs = []
    seen_pr = set()
    for row in inbox["items"]:
        if (not isinstance(row, dict)
                or row.get("project") not in project_names
                or row.get("repository") != registry[row["project"]]
                or row.get("priority") not in SEVERITY
                or row.get("state") != "open"
                or row.get("qa_only") is not False
                or row.get("active_claim") != "not_verified"
                or not isinstance(row.get("number"), int)
                or type(row["number"]) is bool
                or row["number"] <= 0
                or row.get("url") !=
                "https://github.com/" + row["repository"] + "/issues/" + str(row["number"])
                or not isinstance(row.get("related_open_prs"), list)
                or len(row["related_open_prs"]) > 3):
            raise ValueError("invalid canonical issue candidate")
        matches = []
        human_only = row["url"] in HUMAN_ONLY
        for reference in row["related_open_prs"]:
            if (not isinstance(reference, dict)
                    or type(reference.get("number")) is not int
                    or not 1 <= reference["number"] <= 100000000
                    or reference.get("url") !=
                    "https://github.com/" + row["repository"] + "/pull/" + str(reference["number"])
                    or reference.get("relationship") not in
                    ("closing_keyword_only", "text_reference_only")):
                raise ValueError("invalid issue-to-PR reference")
            matches.append(reference["url"])
            key = (row["project"], reference["number"])
            if not human_only and key not in seen_pr and len(prs) < MAX_PRS:
                prs.append({"project_id": row["project"], "pr_number": reference["number"]})
                seen_pr.add(key)
        if human_only:
            route = "authorized_infrastructure_owner_required"
            action = "Bevoegde infrastructuureigenaar nodig; geen runner-restart, betaling of claim."
        elif matches:
            route = "review_existing_pr_first"
            action = "Begin bij bestaande PR en controleer eigenaar, vereiste CI en actuele scope."
        elif row.get("has_assignee"):
            route = "check_assignee_and_current_claim"
            action = "GitHub heeft een assignee; controleer daarnaast actuele queueclaim, PR's en bestanden."
        else:
            route = "verify_ownership_and_scope"
            action = "Controleer actuele claims, branches en bestandscope vóór zelfstandige productimplementatie."
        sampled.append({
            "project_id": row["project"], "priority": row["priority"],
            "issue_url": row["url"], "title": str(row.get("title", ""))[:180],
            "has_github_assignee": bool(row.get("has_assignee")),
            "existing_pr_links_unverified": matches,
            "next_step": route, "action": action,
            "owner_verified": False, "issue_fixed": False,
            "dispatch_authorized": False, "merge_authorized": False,
            "inspected_prs": [],
        })
    # The inbox already interleaves P0 before P1 across projects. Owner-only
    # infrastructure issues remain visible without consuming an inspection slot.
    degraded = inbox.get("stopped_after_rate_or_auth_denial") is True
    for cov in inbox["coverage"]:
        if (not isinstance(cov, dict) or cov.get("project") not in project_names
                or cov.get("status") not in ("partial", "degraded", "unavailable")):
            raise ValueError("invalid issue coverage")
    selected = [] if degraded else prs
    inspected = (observe_prs(registry, selected, token=token) if selected
                 else {"schema": "zsentinel-pr-delivery-desk-v1",
                       "items": [], "request_count": 0, "maximum_requests": 12,
                       "merge_authorized": False, "deploy_authorized": False,
                       "writes_performed": False})
    if (inspected.get("schema") != "zsentinel-pr-delivery-desk-v1"
            or inspected.get("merge_authorized") is not False
            or inspected.get("writes_performed") is not False
            or not isinstance(inspected.get("items"), list)
            or len(inspected["items"]) > MAX_PRS):
        raise ValueError("invalid exact-head PR source")
    by_link = {}
    selected_urls = {
        "https://github.com/" + registry[pr["project_id"]] +
        "/pull/" + str(pr["pr_number"]) for pr in selected
    }
    for entry in inspected["items"]:
        if (not isinstance(entry, dict) or entry.get("merge_authorized") is not False
                or entry.get("url") not in selected_urls
                or entry.get("next_step") not in NEXT_ACTION_NL
                or entry["url"] in by_link):

            raise ValueError("untrusted exact-head PR receipt")
        by_link[entry["url"]] = entry
    for row in sampled:
        for link in row["existing_pr_links_unverified"]:
            if link not in by_link:
                continue
            evidence = by_link[link]
            complete = evidence.get("coverage") == "bounded_exact_head_review_and_checks"
            ci = evidence.get("ci") if complete else None
            legacy = evidence.get("legacy_status_contexts") if complete else None
            ci = ci if isinstance(ci, dict) else {}
            legacy = legacy if isinstance(legacy, dict) else {}
            flagged = []
            for origin, bucket, states in (
                ("check", ci, ("failed", "pending", "inconclusive")),
                ("status", legacy, ("failed", "pending"))):
                for state in states:
                    names = bucket.get(state, [])
                    if (not isinstance(names, list) or len(names) > 8
                            or any(type(name) is not str or len(name) > 180
                                   for name in names)):
                        raise ValueError("untrusted bounded CI evidence")
                    flagged.extend({"origin": origin, "state": state,
                                    "name": name} for name in names)
            row["inspected_prs"].append({
                "url": link,
                "head_sha": evidence.get("head_sha") if complete else None,
                "coverage": evidence.get("coverage") if complete else "source_incomplete",
                "action": NEXT_ACTION_NL[evidence["next_step"]] if complete
                          else "Onvolledige/gewijzigde PR-bron: eigenaar moet opnieuw controleren.",
                "combined_legacy_status": legacy.get("combined_observed", "unknown"),
                "ci_attention": flagged[:16],
                "issue_fixed": False, "owner_verified": False,
                "merge_authorized": False,
            })
    return {
        "schema": "zsentinel-issue-delivery-board-v1",
        "observed_at": inbox["observed_at"],
        "projects": list(project_names),
        "coverage": inbox["coverage"],
        "issue_sample_truncated": bool(inbox.get("truncated")),
        "qa_excluded": inbox.get("suppressed_qa_only", 0),
        "issue_source_requests": inbox["request_count"],
        "exact_head_pr_requests": inspected["request_count"],
        "source_budget_maximum": len(project_names)*3 + MAX_PRS*5,
        "issue_count": len(sampled),
        "items": sampled,
        "inspected_pr_count": len(inspected["items"]),
        "stopped_after_permission_or_rate_limit": degraded,
        "ownership_verified": False, "claims_taken": False,
        "dispatch_authorized": False, "merge_authorized": False,
        "deploy_authorized": False, "writes_performed": False,
    }


def attach_notion_work_visibility(board, observed):
    """Show existing active Notion work without claiming file/lease ownership."""
    if (not isinstance(board, dict)
            or board.get("schema") != "zsentinel-issue-delivery-board-v1"
            or board.get("ownership_verified") is not False
            or board.get("claims_taken") is not False
            or board.get("writes_performed") is not False
            or not isinstance(observed, dict)
            or observed.get("source") != "notion_readonly_data_source_pages"
            or observed.get("coverage") not in ("incomplete", "current_pages_only")
            or observed.get("claim_scope_verified") is not False
            or observed.get("task_inventory_verified") is not False
            or observed.get("can_dispatch") is not False
            or observed.get("can_release_claim") is not False
            or not isinstance(observed.get("active_project_counts"), dict)):
        raise ValueError("unverified or invalid Notion active-work source")
    from datetime import datetime, timedelta, timezone
    stamp = observed_time(observed.get("observed_at"))
    if stamp > datetime.now(timezone.utc) + timedelta(minutes=2):
        raise ValueError("Notion active-work source clock invalid")
    counts = observed["active_project_counts"]
    if len(counts) > 50 or any(
        type(project) is not str or len(project) > 100 or
        type(amount) is not int or amount < 0 or amount > 400
        for project, amount in counts.items()
    ):
        raise ValueError("untrusted Notion project active counters")
    found = {}
    for project in board["projects"]:
        amount = counts.get(project)
        # Zero Notion status hits is NOT a complete file-level claim read.
        found[project] = amount if amount is not None and amount > 0 else None
    board["notion_active_work_observation"] = {
        "source": "notion_readonly_active_status_only",
        "observed_at_utc": stamp.isoformat(),
        "coverage": observed["coverage"],
        "active_project_min": found,
        "file_scope_verified": False,
        "lease_owner_verified": False,
        "can_dispatch": False, "writes_performed": False,
    }
    for item in board["items"]:
        seen = found[item["project_id"]]
        item["notion_active_work_min"] = seen
        if seen is not None:
            item["action"] = (
                "Notion toont minstens " + str(seen) +
                " actief werkpakket(ten) in dit project. Controleer actuele "
                "worker/file-claim en bestaande PR vóór productwerk. " +
                item["action"]
            )
    return board


def render_html(board):
    if (board.get("schema") != "zsentinel-issue-delivery-board-v1"
            or board.get("writes_performed") is not False
            or board.get("dispatch_authorized") is not False):
        raise ValueError("invalid workboard")
    from zsentinel_product_issue_inbox import STYLE
    from zsentinel_issue_delivery_ui import (
        controls, script_tag, followup_controls, INTERACTIVE_CSS,
    )
    progress = board.get("progress_since_previous")
    notion = board.get("notion_active_work_observation")
    notion_panel = ""
    if notion is not None:
        if (not isinstance(notion, dict)
                or notion.get("file_scope_verified") is not False
                or notion.get("can_dispatch") is not False):
            raise ValueError("untrusted Notion visibility")
        notion_panel = (
            '<section aria-label="Notion bestaand actief werk"><h2>'
            'Notion: bestaand actief projectwerk</h2><p>'
            'Actieve statussen zijn geen bestandseigendom of vrije claim. '
            'De telling is een ondergrens, ook bij lege projecten.</p><ul>' +
            "".join('<li>' + escape(pid) + ': ' +
                    ('minstens ' + str(value) + ' actief'
                     if value is not None else 'ONBEKEND') + '</li>'
                    for pid, value in notion["active_project_min"].items()) +
            '</ul></section>'
        )
    comparison = {}
    progress_panel = ""
    if progress is not None:
        if (not isinstance(progress, dict)
                or progress.get("schema") != "zsentinel-issue-progress-v1"
                or progress.get("closed_issues_verified") != 0
                or progress.get("merge_authorized") is not False):
            raise ValueError("untrusted historical issue comparison")
        comparison = {row["issue_url"]: row for row in progress["observed_changes"]}
        older = "".join(
            '<li><a href="' + escape(entry["issue_url"], quote=True) +
            '" rel="noopener noreferrer" target="_blank">' +
            escape(entry["title"]) + '</a> · ' +
            ('BRON NIET BESCHIKBAAR; alleen eerder bewijs aanwezig'
             if entry["observation"] == "current_source_unavailable_previous_state_only"
             else 'niet in huidige beperkte steekproef') +
            '; nooit gesloten of opgelost bewezen.</li>'
            for entry in progress["prior_issues_outside_current_sample"])
        progress_panel = (
            '<section aria-label="Voortgang sinds vorige bronmomentopname">'
            '<h2>Verandering sinds vorige momentopname</h2><p class="notice">'
            'Dit is GEEN complete backlog of sluitingscontrole. '
            'Een ontbrekend issue is nooit automatisch opgelost.</p>'
            '<p>Bronmoment eerder: ' +
            escape(progress["previous_observed_at_utc"]) +
            ' · Nieuw in steekproef: ' + str(progress["newly_sampled_count"]) +
            ' · Gewijzigde broninformatie: ' +
            str(progress["changed_source_receipts_count"]) +
            ' · Niet in huidige steekproef: ' +
            str(progress["previously_sampled_missing_count"]) +
            ' · Huidige bron onbereikbaar (alleen historisch): ' +
            str(progress["previously_sampled_unverifiable_count"]) +
            '</p>' +
            ('<details><summary>Eerder gezien, nu niet waargenomen '
             '(NIET gesloten)</summary><ul>' + older +
             '</ul></details>' if older else "") + '</section>')
    sections = []
    for level in SEVERITY:
        cards = []
        for item in board["items"]:
            if item["priority"] != level:
                continue
            observed = comparison.get(item["issue_url"])
            change_key = ("not-compared" if progress is None else
                          "sourceunknown" if observed is None or
                          not observed["source_comparable"] else
                          "new" if observed["observation"] == "newly_sampled_not_new_issue"
                          else "changed" if observed["observed_changes"] else "unchanged")
            change_detail = ("<p><strong>Bronverandering:</strong> " +
                             escape(", ".join(observed["observed_changes"]) or
                                    observed["observation"]) + "</p>"
                             if observed is not None else "")
            pr_links = "".join(
                '<a href="' + escape(link, quote=True) +
                '" target="_blank" rel="noopener noreferrer">Bestaande PR</a> '
                for link in item["existing_pr_links_unverified"])
            evidence = "".join(
                '<li><a href="' + escape(pr["url"], quote=True) +
                '" rel="noopener noreferrer" target="_blank">Bestaande PR</a> · ' +
                escape(pr["action"]) + " · head " +
                escape((pr["head_sha"] or "ONBEKEND")[:12]) +
                " · klassieke status: " + escape(str(pr["combined_legacy_status"])) +
                " · nooit oplossings- of mergebewijs" +
                ("<ul>" + "".join(
                    "<li>" + escape(check["origin"] + " " + check["state"] +
                                    ": " + check["name"]) + "</li>"
                    for check in pr["ci_attention"]) + "</ul>"
                 if pr["ci_attention"] else "") + "</li>"
                for pr in item["inspected_prs"])
            cards.append(
                '<article class="issue" data-change="' +
                escape(change_key, quote=True) + '" data-project="' +
                escape(item["project_id"], quote=True) + '" data-priority="' +
                escape(item["priority"], quote=True) + '" data-route="' +
                escape(item["next_step"], quote=True) + '" data-search="' +
                escape((item["title"] + " " + item["project_id"] + " " +
                        item["action"]).lower(), quote=True) + '" data-title="' +
                escape(item["title"], quote=True) + '" data-issue-url="' +
                escape(item["issue_url"], quote=True) +
                '"><a class="issue-title" href="' +
                escape(item["issue_url"], quote=True) +
                '" target="_blank" rel="noopener noreferrer">' +
                escape(item["title"]) + '</a><span class="tag">' +
                escape(item["project_id"]) + '</span><span class="tag">' +
                escape(item["priority"]) + '</span><p><strong>Probleem:</strong> ' +
                escape(item["next_step"]) + '</p><p><strong>Actie:</strong> ' +
                escape(item["action"]) + '</p><p><strong>Status:</strong> eigendom, '
                'bestandscope en herstel niet geverifieerd.</p><p>' +
                pr_links + '</p>' + ('<details><summary>Exacte PR-bron</summary><ul>' +
                evidence + '</ul></details>' if evidence else '') +
                change_detail + followup_controls() + '</article>')
        sections.append('<section data-priority-group="' + escape(level, quote=True) +
                        '"><h2>' + level + ' · ' + str(len(cards)) +
                        ' in beperkte bron · <small data-visible-group-count></small></h2>' + ("".join(cards) or
                        '<p class="muted">Geen kandidaat in deze steekproef; niet bewezen leeg.</p>') +
                        "</section>")
    coverage = "".join(
        "<li>" + escape(str(row["project"])) + " · " +
        escape(str(row["status"])) +
        " · oudste=" + escape(str(row.get("oldest_window", "unknown"))[:90]) +
        " · recent=" + escape(str(row.get("recent_window", "unknown"))[:90]) +
        " · bestaande PR's=" +
        escape(str(row.get("open_pr_window", "unknown"))[:90]) + "</li>"
        for row in board["coverage"])
    missing_source = [
        row["project"] for row in board["coverage"]
        if row["status"] != "partial"
    ]
    source_warning = (
        '<p class="notice" role="alert"><strong>GitHub-bron onvolledig.</strong> '
        'Voor ' + escape(", ".join(missing_source)) +
        ' zijn actuele issues/PR’s NIET volledig waargenomen. '
        'Historische observaties betekenen geen sluiting of herstel.</p>'
        if missing_source else
        '<p class="muted">Alle gekozen projecten hebben gedeeltelijke '
        'GitHub-bronobservaties; geen compleet backlogbewijs.</p>')
    return (
        '<!doctype html><html lang="nl"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="referrer" content="no-referrer">'
        '<meta http-equiv="Content-Security-Policy" '
        'content="default-src &#39;none&#39;; script-src &#39;unsafe-inline&#39;; '
        'object-src &#39;none&#39;; base-uri &#39;none&#39;; '
        'style-src &#39;unsafe-inline&#39;; '
        'connect-src &#39;none&#39;; img-src &#39;none&#39;; form-action &#39;none&#39;">'
        '<title>zSentinel · Productherstelbord</title><style>' + STYLE +
        INTERACTIVE_CSS +
        '</style></head><body data-observed-at="' +
        escape(board["observed_at"], quote=True) +
        '"><header><h1>Vliegende keep · productherstelbord</h1>'
        '<p>Open problemen eerst, bestaande PR vóór dubbel bouwen.</p>'
        '<p class="notice">Bronsteekproef, geen eigendomsbewijs. '
        'Geen claim, QA-ronde, runneractie, review, merge of deploy vanuit dit bord.</p>'
        '<p class="muted">Bronmoment UTC: ' + escape(board["observed_at"]) +
        ' · PR-bronreads: ' + str(board["exact_head_pr_requests"]) +
        ' · productissues: ' + str(board["issue_count"]) + '</p>' +
        source_warning + '</header><main>' +
        controls(board["projects"]) + notion_panel + progress_panel + "".join(sections) + '<section><details><summary>Brondekking en '
        'onzekerheid</summary><ul>' + coverage + '</ul><p>Zelfs volledige '
        'steekproef bewijst geen complete backlog; lokale Notion-/fileclaims '
        'niet geverifieerd.</p></details></section></main><footer>Alle links openen '
        'GitHub. Bron- en mergeautorisatie blijven gescheiden.</footer>' +
        script_tag() + '</body></html>'
    )


def render_markdown(board):
    from zsentinel_pr_delivery import _markdown_literal
    lines = ["# zSentinel · Productherstelbord",
             "Bronmoment UTC: " + _markdown_literal(board["observed_at"], 40),
             "Beperkte issue-/PR-steekproef. Geen eigenaar, scope, fix of merge goedgekeurd."]
    progress = board.get("progress_since_previous")
    notion = board.get("notion_active_work_observation")
    change_map = {}
    if notion:
        lines.extend(["", "## Notion actief werk (statuswaarneming, geen file-owner)",
                      "Bron: " + _markdown_literal(notion["observed_at_utc"])])
        for pid, amount in notion["active_project_min"].items():
            lines.append("- " + _markdown_literal(pid) + ": " +
                         ("minstens " + str(amount) + " actief" if amount
                          else "ONBEKEND; geen vrijgavebewijs"))
    if progress is not None:
        change_map = {entry["issue_url"]: entry for entry in progress["observed_changes"]}
        lines.extend([
            "", "## Verandering sinds vorige bronmomentopname",
            "- Vorige bron: " + _markdown_literal(progress["previous_observed_at_utc"], 50),
            "- Nieuw gesampled (niet nieuw issue): " + str(progress["newly_sampled_count"]),
            "- Bronwijzigingen bij bestaande issues: " + str(progress["changed_source_receipts_count"]),
            "- Eerder gezien maar nu uit steekproef (NIET gesloten): " +
            str(progress["previously_sampled_missing_count"]),
            "- Huidige bron niet bereikbaar: enkel oudere observatie: " +
            str(progress["previously_sampled_unverifiable_count"]),
            "- Geen totale backlog of actuele sluitingsstatus bewezen."
        ])
    for source in board["coverage"]:
        if source["status"] != "partial":
            lines.append("- ACTUELE BRON ONBESCHIKBAAR/ONVOLLEDIG: " +
                         _markdown_literal(source["project"]) +
                         " (" + _markdown_literal(source["status"]) +
                         "); geen nul-issues of herstelbewijs.")
    for item in board["items"]:
        lines += ["", "## " + item["priority"] + " · [" +
                  _markdown_literal(item["title"]) + "](" + item["issue_url"] + ")",
                  "- **Probleem:** " + _markdown_literal(item["next_step"]),
                  "- **Actie:** " + _markdown_literal(item["action"]),
                  "- **Status:** owner/fileclaim onbekend; issue niet geverifieerd opgelost."]
        changed = change_map.get(item["issue_url"])
        if changed is not None:
            lines.append("- **Bronvergelijking:** " +
                         _markdown_literal(changed["observation"]) +
                         ("; " + ", ".join(changed["observed_changes"])
                          if changed["observed_changes"] else ""))
        for pr in item["inspected_prs"]:
            lines.append("- **Bestaande PR:** [" + _markdown_literal(pr["url"]) +
                         "](" + pr["url"] + "); " + _markdown_literal(pr["action"]) +
                         "; exacte head " + (pr["head_sha"] or "ONBEKEND")[:12] +
                         "; klassiek gecombineerd " +
                         _markdown_literal(pr["combined_legacy_status"]))
            for check in pr["ci_attention"]:
                lines.append("  - " + _markdown_literal(
                    check["origin"] + " " + check["state"] + ": " + check["name"]))
        if item["existing_pr_links_unverified"] and not item["inspected_prs"]:
            lines.append("- **PR-bron:** verwijzingen bestaan, exacte status niet vastgesteld.")
    if progress is not None:
        for historic in progress["prior_issues_outside_current_sample"]:
            lines.extend([
                "", "- " + ("Huidige bron ONBESCHIKBAAR, enkel oude waarneming"
                       if historic["observation"] ==
                          "current_source_unavailable_previous_state_only" else
                       "Eerder gezien, nu niet in begrensde steekproef") +
                " (NIET gesloten): [" +
                _markdown_literal(historic["title"]) + "](" +
                historic["issue_url"] + ")"
            ])
    lines += ["", "Brondekking: " + ", ".join(
        _markdown_literal(row["project"] + "=" + row["status"])
        for row in board["coverage"]), "",
        "_Geen claims, automatische acties of toestemming tot productie-integratie._", ""]
    return "\n".join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(description="One-shot product issue + existing PR workboard")
    p.add_argument("--registry", type=Path, default=ROOT / "projects.json")
    p.add_argument("--project", action="append", required=True)
    p.add_argument("--max-items", type=int, default=MAX_ISSUES)
    p.add_argument("--previous", type=Path,
                   help="optional earlier local JSON workboard snapshot; never a closure proof")
    p.add_argument("--read-notion-claims", action="store_true",
                   help="optional read-only Notion active statuses; never file/lease owner proof")
    p.add_argument("--notion-data-source",
                   help="canonical Notion queue data source UUID for explicit source read")
    p.add_argument("--format", choices=("html", "json", "md"), default="html")
    args = p.parse_args(argv)
    try:
        if (not 1 <= len(args.project) <= MAX_PROJECTS
                or len(set(args.project)) != len(args.project)
                or not 1 <= args.max_items <= MAX_ISSUES):
            raise ValueError("select one to three distinct projects and at most 24 issues")
        registry = canonical_projects(args.registry)
        inbox = collect(registry, args.project, os.getenv("GITHUB_TOKEN"), args.max_items)
        board = build_workboard(inbox, registry, os.getenv("GITHUB_TOKEN"))
        if args.read_notion_claims:
            from zsentinel_notion_claim_observer import (
                observe_claims, project_labels,
            )
            mapping = project_labels(args.registry, registry)
            notion_source = observe_claims(
                args.notion_data_source or os.getenv("NOTION_QUEUE_DATA_SOURCE_ID"),
                os.getenv("NOTION_TOKEN"), known_projects=mapping)
            attach_notion_work_visibility(board, notion_source)
        elif args.notion_data_source:
            raise ValueError("--notion-data-source requires --read-notion-claims")
        if args.previous:
            board["progress_since_previous"] = progress_since(
                board, load_private_prior(args.previous))
        if args.format == "json":
            output = json.dumps(board, sort_keys=True, ensure_ascii=True, indent=2)
        elif args.format == "md":
            output = render_markdown(board)
        else:
            output = render_html(board)
        print(output)
        return 0 if all(x["status"] == "partial" for x in board["coverage"]) else 3
    except (ValueError, TypeError, KeyError, OSError, json.JSONDecodeError) as error:
        p.exit(2, "zSentinel herstelbord bron onvolledig/ongeldig: " +
               str(error)[:120] + "\n")


if __name__ == "__main__":
    raise SystemExit(main())

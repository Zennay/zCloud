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

from zsentinel_product_issue_inbox import ROOT, canonical_projects, collect
from zsentinel_pr_delivery import NEXT_ACTION_NL, observe_prs

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
    for entry in inspected["items"]:
        if (not isinstance(entry, dict) or entry.get("merge_authorized") is not False
                or entry.get("url") not in [r["url"] for r in
                    ({"url": "https://github.com/" + registry[pr["project_id"]] +
                      "/pull/" + str(pr["pr_number"])} for pr in selected)]
                or entry.get("next_step") not in NEXT_ACTION_NL):
            raise ValueError("untrusted exact-head PR receipt")
        by_link[entry["url"]] = entry
    for row in sampled:
        for link in row["existing_pr_links_unverified"]:
            if link not in by_link:
                continue
            evidence = by_link[link]
            complete = evidence.get("coverage") == "bounded_exact_head_review_and_checks"
            row["inspected_prs"].append({
                "url": link,
                "head_sha": evidence.get("head_sha") if complete else None,
                "coverage": evidence.get("coverage") if complete else "source_incomplete",
                "action": NEXT_ACTION_NL[evidence["next_step"]] if complete
                          else "Onvolledige/gewijzigde PR-bron: eigenaar moet opnieuw controleren.",
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


def render_html(board):
    if (board.get("schema") != "zsentinel-issue-delivery-board-v1"
            or board.get("writes_performed") is not False
            or board.get("dispatch_authorized") is not False):
        raise ValueError("invalid workboard")
    from zsentinel_product_issue_inbox import STYLE
    from zsentinel_issue_delivery_ui import (
        controls, script_tag, followup_controls, INTERACTIVE_CSS,
    )
    sections = []
    for level in SEVERITY:
        cards = []
        for item in board["items"]:
            if item["priority"] != level:
                continue
            pr_links = "".join(
                '<a href="' + escape(link, quote=True) +
                '" target="_blank" rel="noopener noreferrer">Bestaande PR</a> '
                for link in item["existing_pr_links_unverified"])
            evidence = "".join(
                "<li>" + escape(pr["action"]) + " · head " +
                escape((pr["head_sha"] or "ONBEKEND")[:12]) +
                " · nooit oplossings- of mergebewijs</li>"
                for pr in item["inspected_prs"])
            cards.append(
                '<article class="issue" data-project="' +
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
                followup_controls() + '</article>')
        sections.append('<section data-priority-group="' + escape(level, quote=True) +
                        '"><h2>' + level + ' · ' + str(len(cards)) +
                        ' in beperkte bron · <small data-visible-group-count></small></h2>' + ("".join(cards) or
                        '<p class="muted">Geen kandidaat in deze steekproef; niet bewezen leeg.</p>') +
                        "</section>")
    coverage = "".join("<li>" + escape(str(row["project"])) +
                       ": " + escape(str(row["status"])) + "</li>"
                       for row in board["coverage"])
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
        '</style></head><body><header><h1>Vliegende keep · productherstelbord</h1>'
        '<p>Open problemen eerst, bestaande PR vóór dubbel bouwen.</p>'
        '<p class="notice">Bronsteekproef, geen eigendomsbewijs. '
        'Geen claim, QA-ronde, runneractie, review, merge of deploy vanuit dit bord.</p>'
        '<p class="muted">Bronmoment UTC: ' + escape(board["observed_at"]) +
        ' · PR-bronreads: ' + str(board["exact_head_pr_requests"]) +
        ' · productissues: ' + str(board["issue_count"]) + '</p></header><main>' +
        controls(board["projects"]) + "".join(sections) + '<section><details><summary>Brondekking en '
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
    for item in board["items"]:
        lines += ["", "## " + item["priority"] + " · [" +
                  _markdown_literal(item["title"]) + "](" + item["issue_url"] + ")",
                  "- **Probleem:** " + _markdown_literal(item["next_step"]),
                  "- **Actie:** " + _markdown_literal(item["action"]),
                  "- **Status:** owner/fileclaim onbekend; issue niet geverifieerd opgelost."]
        for pr in item["inspected_prs"]:
            lines.append("- **Bestaande PR:** [" + _markdown_literal(pr["url"]) +
                         "](" + pr["url"] + "); " + _markdown_literal(pr["action"]) +
                         "; exacte head " + (pr["head_sha"] or "ONBEKEND")[:12])
        if item["existing_pr_links_unverified"] and not item["inspected_prs"]:
            lines.append("- **PR-bron:** verwijzingen bestaan, exacte status niet vastgesteld.")
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

#!/usr/bin/env python3
"""zSentinel's source-bound next-problem shortlist for the portfolio operator.

The P0/P1 issue sample is *not* a claim inventory. This module surfaces concrete
next inspection targets while preventing a PR reference, an empty queue or a
source gap from being mistaken for permission to start a second implementation.
It never dispatches, merges, retries, assigns, or writes external state.
"""
from __future__ import annotations

import re
from html import escape

MAX_NEXT = 8
ISSUE_URL = re.compile(r"^https://github[.]com/Zennay/[A-Za-z0-9_.-]+/issues/[1-9][0-9]{0,8}$")
PR_URL = re.compile(r"^https://github[.]com/Zennay/[A-Za-z0-9_.-]+/pull/[1-9][0-9]{0,8}$")
PRIORITY = {"P0": 0, "P1": 1, "P2": 2, "Unspecified": 3}
# Explicitly known owner-only infrastructure gates must never monopolize the
# product implementation shortlist or masquerade as free worker tasks.
OWNER_GATED_ISSUES = frozenset({
    "https://github.com/Zennay/zCloud/issues/1326",  # VPS runner listener
    "https://github.com/Zennay/zCloud/issues/1523",  # GitHub account billing
})


def next_problems(summary, decision, selected):
    """Rank sampled product issues fairly across inspected repositories.

    All candidates require an ownership and file-scope recheck. In particular
    even a fully observed queue only contains *aggregate* ownership here.
    """
    if (not isinstance(summary, dict)
            or summary.get("schema") != "zsentinel-portfolio-overview-v1"
            or summary.get("queue_owner_authorized") is not False
            or summary.get("actions_performed") is not False
            or not isinstance(selected, list) or not 1 <= len(selected) <= 3
            or len(selected) != len(set(selected))):
        raise ValueError("untrusted portfolio selection for problem shortlist")
    rows = summary.get("rows")
    if not isinstance(rows, list) or len(rows) > 18:
        raise ValueError("untrusted project registry")
    mapping = {row.get("project_id"): row for row in rows if isinstance(row, dict)}
    if len(mapping) != len(rows) or any(pid not in mapping for pid in selected):
        raise ValueError("unknown or duplicate selected project")
    triage = decision.get("open_product_issue_triage")
    if not isinstance(triage, dict):
        raise ValueError("missing canonical issue triage")
    coverage = triage.get("coverage")
    if coverage not in ("partial", "degraded", "stale", "unknown"):
        raise ValueError("unsupported issue source coverage")
    issues = triage.get("issues")
    if not isinstance(issues, list) or len(issues) > 8:
        raise ValueError("unbounded issue source sample")
    collected = {pid: [] for pid in selected}
    held_issues = []
    seen = set()
    if coverage != "stale":
        for issue in issues:
            if not isinstance(issue, dict):
                raise ValueError("invalid issue source row")
            pid = issue.get("project_id")
            url = issue.get("issue_url")
            tier = issue.get("priority")
            links = issue.get("open_pr_candidates")
            if (pid not in collected or tier not in PRIORITY
                    or not isinstance(url, str) or not ISSUE_URL.fullmatch(url)
                    or not isinstance(links, list) or len(links) > 3
                    or issue.get("status") != "inspection_only_ownership_unverified"):
                raise ValueError("unverified cross-project issue source")
            repo = mapping[pid].get("github_repo")
            prefix = "https://github.com/" + repo
            if not url.startswith(prefix + "/issues/") or url in seen:
                raise ValueError("issue does not match canonical repository")
            if (not all(isinstance(link, str) and PR_URL.fullmatch(link)
                        and link.startswith(prefix + "/pull/") for link in links)):
                raise ValueError("invalid candidate PR reference")
            seen.add(url)
            if url in OWNER_GATED_ISSUES:
                held_issues.append({
                    "project_id": pid,
                    "priority": tier,
                    "title": issue.get("title", "")[:180],
                    "issue_url": url,
                    "next_step": "authorized_infrastructure_owner_required",
                    "action": "Bevoegde runner- of billing-eigenaar nodig; geen worker-retry, betaling of productclaim.",
                    "dispatch_authorized": False,
                })
                continue
            row = mapping[pid]
            owned, notion = row.get("owned"), row.get("notion_active_status_min")
            if owned is not None and (type(owned) is not int or owned < 0):
                raise ValueError("invalid local ownership count")
            if notion is not None and (type(notion) is not int or notion < 0):
                raise ValueError("invalid Notion ownership count")
            if links:
                route = "review_existing_pr_first"
                action = "Controleer eerst de bestaande PR, actuele eigenaar, CI en review; geen dubbel werk starten."
            elif owned or notion:
                route = "verify_active_owner_and_file_scope"
                action = "Er is actief projectwerk: controleer de actuele claim, bestanden en PR's vóór een nieuwe branch."
            else:
                route = "scope_and_owner_verification_required"
                action = "Controleer actuele claim/PR/bestandspaden; maak daarna alleen bij vrije scope een productfix."
            if row.get("github_observation") not in ("partial_both_sources", "issue_sample_only"):
                route = "source_incomplete_owner_review"
                action = "GitHub-dekking onvolledig: verifieer het issue, open PR's en de huidige eigenaar."
            if tier == "Unspecified":
                action = "Geen prioriteit bevestigd. Verifieer ernst, eigenaar, open PR’s en padscope voordat productwerk start."
            collected[pid].append({
                "project_id": pid,
                "priority": tier,
                "title": issue.get("title", "")[:180],
                "issue_url": url,
                "related_open_prs": sorted(set(links)),
                "next_step": route,
                "action": action,
                "ownership_verified": False,
                "dispatch_authorized": False,
                "merge_authorized": False,
            })
    # Preserve P0-before-P1 while fairly interleaving projects. Within the
    # same project and tier, finish a referenced PR before opening a duplicate.
    for values in collected.values():
        values.sort(key=lambda item: (
            PRIORITY[item["priority"]],
            0 if item["related_open_prs"] else 1,
            item["issue_url"],
        ))
    shortlist = []
    for tier in ("P0", "P1", "P2", "Unspecified"):
        active = {pid: [x for x in collected[pid] if x["priority"] == tier] for pid in selected}
        while any(active.values()) and len(shortlist) < MAX_NEXT:
            for pid in selected:
                if active[pid]:
                    shortlist.append(active[pid].pop(0))
                    if len(shortlist) == MAX_NEXT:
                        break
    incidents = decision.get("incidents")
    if not isinstance(incidents, list):
        raise ValueError("invalid incident feed")
    owner_blockers = []
    for incident in incidents:
        if not isinstance(incident, dict):
            raise ValueError("invalid incident row")
        if incident.get("human_escalation") is True:
            owner_blockers.append({
                "project_id": incident.get("project_id"),
                "severity": incident.get("severity"),
                "problem": incident.get("problem"),
                "action": incident.get("action"),
                "dispatch_authorized": False,
            })
    owner_blockers = sorted(owner_blockers,
                            key=lambda x: (0 if x["severity"] == "P0" else 1,
                                           str(x["project_id"]), str(x["problem"])))[:5]
    return {
        "schema": "zsentinel-next-problems-v1",
        "source_coverage": coverage,
        "selection_scope": "selected_github_window_only",
        "selected_projects": list(selected),
        "projects_outside_window": len(rows) - len(selected),
        "sampled_issue_count": len(issues),
        "items": shortlist,
        "owner_only_incidents": owner_blockers,
        "owner_only_issue_candidates": sorted(
            held_issues, key=lambda x: (PRIORITY[x["priority"]], x["issue_url"])),
        "ownership_verified": False,
        "dispatch_authorized": False,
        "writes_performed": False,
        "empty_means_healthy": False,
    }


def problems_html(result):
    if (result.get("schema") != "zsentinel-next-problems-v1"
            or result.get("dispatch_authorized") is not False
            or result.get("writes_performed") is not False):
        raise ValueError("untrusted problem picker output")
    rows = []
    for issue in result["items"]:
        url = escape(issue["issue_url"], quote=True)
        title = escape(issue["title"])
        links = " ".join(
            '<a href="' + escape(link, quote=True) +
            '" target="_blank" rel="noopener noreferrer">bestaande PR</a>'
            for link in issue["related_open_prs"]
        )
        rows.append(
            '<article class="panel read"><h3>' + escape(issue["priority"]) +
            ' · <a href="' + url +
            '" target="_blank" rel="noopener noreferrer">' + title +
            '</a></h3><p>' + escape(issue["project_id"]) + ' · ' +
            escape(issue["action"]) + '</p><p>' + links + '</p></article>'
        )
    if not rows:
        rows.append('<p class="empty">Geen betrouwbare productissue-kandidaten in dit beperkte venster. '
                    'Dit is GEEN bewijs dat er geen problemen zijn.</p>')
    held = "".join(
        '<li><a href="' + escape(item["issue_url"], quote=True) +
        '" target="_blank" rel="noopener noreferrer">' +
        escape(item["priority"] + " " + item["title"]) + '</a> — ' +
        escape(item["action"]) + '</li>'
        for item in result["owner_only_issue_candidates"]
    )
    blockers = "".join(
        '<li>' + escape(str(item["severity"])) + ' ' +
        escape(str(item["project_id"])) + ': ' +
        escape(str(item["problem"])) + ' — ' +
        escape(str(item["action"])) + '</li>'
        for item in result["owner_only_incidents"]
    )
    return ('<section><h2>Volgende problemen · niet toegewezen</h2>'
            '<p class="banner">Eerst P0/P1, daarna P2 en zonder prioriteitslabel. Bestaande PR’s vóór dubbele implementatie. '
            'Steekproef uit alleen het huidige GitHub-venster; projectclaims, '
            'bestandseigendom en releasegates niet geverifieerd. '
            'Dit onderdeel voert NOOIT zelf werk uit.</p><div class="grid">' +
            "".join(rows) + '</div>' +
            ('<details><summary>Issueblokkades die een infrastructuureigenaar vereisen</summary><ul>' +
             held + '</ul></details>' if held else '') +
            ('<details><summary>CI-/runtime-incidenten die eigenaaractie vereisen</summary><ul>' +
             blockers + '</ul></details>' if blockers else '') + '</section>')

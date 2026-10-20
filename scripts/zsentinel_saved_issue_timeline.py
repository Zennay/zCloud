#!/usr/bin/env python3
"""Issue-first human handoff across existing source-bound zSentinel ZIP dossiers.

Uses the same 2-12 immutable saved snapshots as the historical PR timeline,
never reads live GitHub/Notion or makes a claim/merge/deployment decision.
"""
from __future__ import annotations

import argparse
import csv
from html import escape
from io import StringIO
import json
import os
from pathlib import Path
import sys
from zipfile import BadZipFile

from zsentinel_saved_dossier_timeline import build_timeline, PRIORITIES

MAX_BYTES = 1024 * 1024
MAX_ISSUES = 600
RANK = {name: index for index, name in enumerate(PRIORITIES)}


def issue_worklist(report):
    """Group truly linked canonical issue addresses across sampled existing PRs.

    An issue absent from these PR snapshots is unknown, not fixed or free.
    The same issue may have several existing PRs and independent current owners.
    """
    indexed = {}
    unlinked = []
    for candidate in report["entries"]:
        refs = set()
        snapshots = set()
        for seen in candidate["observations"]:
            for url in seen["linked_issue_urls"]:
                refs.add(url)
                snapshots.add(seen["snapshot_index"])
        if not refs:
            unlinked.append({
                "url": candidate["url"],
                "priority": candidate["latest_sample_priority"],
                "last_seen_snapshot": candidate["last_seen_snapshot"],
                "missing_from_final_sample": candidate["missing_from_final_snapshot"],
                "linked_issue_not_observed": True,
                "owner_claim_verified": False,
                "merge_verified": False,
            })
        for url in sorted(refs):
            if "/issues/" not in url:
                raise ValueError("Noncanonical source issue identity")
            issue = indexed.setdefault(url, {
                "issue_url": url,
                "issue_number": int(url.rsplit("/", 1)[-1]),
                "repository": candidate["repository"],
                "linked_existing_prs": [],
                "observed_in_snapshots": set(),
                "read_only": True,
                "issue_open_state_verified": False,
                "owners_verified": False,
                "existing_prs_merged_verified": False,
                "worker_dispatch_authorized": False,
            })
            if url.split("/issues/")[0] != "https://github.com/" + candidate["repository"]:
                raise ValueError("Source issue belongs to a different repository")
            issue["observed_in_snapshots"].update(snapshots)
            issue["linked_existing_prs"].append({
                "pr_url": candidate["url"],
                "pr_number": candidate["number"],
                "source_priority": candidate["latest_sample_priority"],
                "observed_head": candidate["latest_observed_head"],
                "observed_primary_gate": candidate["latest_sample_primary_gate"],
                "observed_head_changes": candidate["observed_head_changes"],
                "observed_gate_changes": candidate["observed_gate_changes"],
                "last_seen_snapshot": candidate["last_seen_snapshot"],
                "missing_from_final_sample": candidate["missing_from_final_snapshot"],
                "owner_claim_verified": False,
                "required_ci_review_verified": False,
                "main_merge_verified": False,
            })
    if len(indexed) > MAX_ISSUES:
        raise ValueError("Source evidence contains too many distinct issue links")
    groups = []
    for issue in indexed.values():
        issue["observed_in_snapshots"] = sorted(issue["observed_in_snapshots"])
        issue["linked_existing_prs"].sort(
            key=lambda x: (RANK[x["source_priority"]], x["pr_number"]))
        issue["highest_source_priority"] = issue["linked_existing_prs"][0]["source_priority"]
        issue["human_next_action"] = (
            "Controleer live de bestaande gekoppelde PR(s), hun actuele ownerclaims, "
            "exacte HEAD, vereiste CI, onafhankelijke review en protected-main "
            "voordat een nieuwe PR of werkclaim wordt begonnen."
        )
        groups.append(issue)
    groups.sort(key=lambda x: (
        RANK[x["highest_source_priority"]],
        -len(x["linked_existing_prs"]), x["repository"], x["issue_number"]))
    unlinked.sort(key=lambda x: (RANK[x["priority"]], x["url"]))
    return {
        "schema": "zsentinel-private-saved-issue-first-handoff-v1",
        "source": "existing_pr_issue_links_in_saved_private_snapshots_not_live",
        "scope": report["scope"],
        "source_snapshots": report["source_snapshots"],
        "total_distinct_saved_prs": report["distinct_pr_candidates"],
        "linked_issue_count": len(groups),
        "pr_without_source_issue_count": len(unlinked),
        "issues": groups,
        "unlinked_prs": unlinked,
        "issue_inventory_known_complete": False,
        "github_open_or_closed_state_verified": False,
        "owner_claim_verified": False,
        "required_ci_review_verified": False,
        "main_merge_verified": False,
        "live_verified": False,
        "writes_performed_to_external_systems": False,
    }


def filter_issue_page(report, *, priority=None, page=1, page_size=30):
    if priority is not None and priority not in RANK:
        raise ValueError("Invalid source-PR priority")
    if (type(page) is not int or not 1 <= page <= 100
            or type(page_size) is not int or not 1 <= page_size <= 100):
        raise ValueError("Invalid issue source page")
    all_rows = report["issues"]
    rows = [g for g in all_rows
            if priority is None or g["highest_source_priority"] == priority]
    first = (page - 1) * page_size
    if first >= len(rows) and (rows or page != 1):
        raise ValueError("Issue page not present in source sample")
    display = dict(report)
    display["issues"] = rows[first:first + page_size]
    display["selection"] = {
        "priority_filter": priority,
        "total_source_issues": len(all_rows),
        "matched_source_issues": len(rows),
        "shown_source_issues": len(display["issues"]),
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, (len(rows) + page_size - 1) // page_size),
        "missing_issue_or_pr_is_never_closed": True,
    }
    return display


def html_content(result):
    e = lambda v: escape(str(v), quote=True)
    cards = []
    for issue in result["issues"]:
        prs = []
        for p in issue["linked_existing_prs"]:
            prs.append(
                '<li><a target="_blank" rel="noopener noreferrer" href="' +
                e(p["pr_url"]) + '">PR #' + str(p["pr_number"]) +
                '</a> — ' + e(p["source_priority"]) + ' · gate ' +
                e(p["observed_primary_gate"]) +
                ' · HEAD ' + e(p["observed_head"] or "ONBEKEND") +
                (' · Verdwenen uit eindsteekproef (NIET gesloten)'
                 if p["missing_from_final_sample"] else '') + '</li>')
        cards.append(
            '<article><h2><a target="_blank" rel="noopener noreferrer" href="' +
            e(issue["issue_url"]) + '">Issue #' + str(issue["issue_number"]) +
            '</a> · ' + e(issue["highest_source_priority"]) + '</h2><p>' +
            str(len(prs)) + ' bestaande PR-link(s) in opgeslagen bronmomenten: ' +
            e(", ".join(str(x) for x in issue["observed_in_snapshots"])) +
            '</p><ol>' + ''.join(prs) + '</ol><p>' +
            e(issue["human_next_action"]) + '</p></article>')
    loose = "".join(
        '<li><a target="_blank" rel="noopener noreferrer" href="' + e(p["url"]) +
        '">' + e(p["priority"]) + ' · ' + e(p["url"]) + '</a></li>'
        for p in result["unlinked_prs"])
    selection = result["selection"]
    return ("""<!doctype html><html lang="nl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; object-src 'none'; img-src 'none'; connect-src 'none'; base-uri 'none'; form-action 'none'">
<title>zSentinel · bestaande PRs per issue</title>
<style>:root{color-scheme:dark;font-family:system-ui}
body{background:#102034;color:#f2f8ff;margin:0;line-height:1.55}
main{max-width:1100px;margin:auto;padding:clamp(16px,4vw,36px)}
article,aside{border:1px solid #5c7a98;border-radius:12px;margin:14px 0;
padding:14px 18px;background:#1b3048}aside{border-color:#efc36c}
article h2{font-size:1.2rem}a{color:#98d8ff}
a:focus-visible{outline:3px solid #f4c86c;outline-offset:3px}
li{margin:8px 0;overflow-wrap:anywhere}details summary{cursor:pointer;padding:10px 0}
@media print{body,aside,article{background:white;color:black}a{color:#075080}}
</style></head><body><main><h1>Issue-eerst · hergebruik bestaande PRs</h1>
<aside><strong>Geen actuele GitHub-issuescan.</strong> Dit zijn alleen de
historische bronissues die al aan waargenomen bestaande PR's gekoppeld waren.
Open/closed, actieve eigenaren, verplichte CI/review en merge zijn onbekend.
Ontbrekende issuekoppelingen mogen geen vrije werkclaim suggereren.</aside><p>
""" + str(selection["shown_source_issues"]) + " issues getoond van " +
str(selection["matched_source_issues"]) + " filtersmatches (" +
str(selection["total_source_issues"]) + " in bron). Pagina " +
str(selection["page"]) + "/" + str(selection["total_pages"]) +
".</p>" + "".join(cards) +
'<details><summary>PRs zonder waargenomen bronissue (' +
str(result["pr_without_source_issue_count"]) +
')</summary><ol>' + loose +
'</ol><p>Dit kan een onvolledige GitHub-zoekscope zijn, geen bewijs van vrij werk.</p></details>' +
"""<footer>Controleer live issue, actuele PR HEAD en actieve ownerclaims
voordat je een bestaand product-PR afrondt of een nieuwe claim start.
Dit dossier doet geen dispatch, merge of deploy.</footer></main></body></html>""")


def markdown_content(result):
    sel = result["selection"]
    lines = ["# zSentinel · issue-eerst bestaande PR-werkrij", "",
             "> Alleen historische private PR-issues; geen owner, CI, review, "
             "GitHub-issue-openstatus, main-merge of live herstel geverifieerd.", "",
             "- Bronissues: " + str(sel["total_source_issues"]) +
             ", getoond: " + str(sel["shown_source_issues"]) +
             " / " + str(sel["matched_source_issues"]) +
             "; pagina " + str(sel["page"]) + "/" + str(sel["total_pages"]), ""]
    for issue in result["issues"]:
        lines.extend([
            "## " + issue["highest_source_priority"] + " [Issue #" +
            str(issue["issue_number"]) + "](" + issue["issue_url"] + ")", "",
            issue["human_next_action"], "",
        ])
        for p in issue["linked_existing_prs"]:
            lines.append("- [Bestaande PR #" + str(p["pr_number"]) + "](" +
                         p["pr_url"] + ") — " + p["source_priority"] +
                         " · oude bron-HEAD " +
                         (p["observed_head"] or "ONBEKEND") +
                         " · gate " + p["observed_primary_gate"] +
                         (" · UIT STEEKPROEF, NIET bewezen gesloten"
                          if p["missing_from_final_sample"] else ""))
        lines.append("")
    lines.extend([
        "## PRs zonder betrouwbare opgeslagen issuekoppeling", "",
    ])
    for p in result["unlinked_prs"]:
        lines.append("- [" + p["priority"] + " · " + p["url"] +
                     "](" + p["url"] + ") — issueverband ONBEKEND.")
    lines.extend(["", "Geen bron- of paginaselectie bewijst een vrije eigenaar, "
                  "vereiste CI of main-merge.", ""])
    return "\n".join(lines)


def csv_content(result):
    source = StringIO(newline="")
    writer = csv.writer(source, delimiter=";", quotechar='"', lineterminator="\r\n")
    writer.writerow([
        "Issue bron URL", "Issue bronprioriteit", "Bestaande PR bron URL",
        "PR bronprioriteit", "PR laatst geobserveerde HEAD", "PR hoofdgate",
        "Laatst gezien bronnummer", "Verdwenen uit eindsteekproef",
        "Menselijke vervolgactie", "Huidige claim/CI/review",
        "Main merge", "Issue status live",
    ])
    for issue in result["issues"]:
        for p in issue["linked_existing_prs"]:
            writer.writerow([
                issue["issue_url"], issue["highest_source_priority"], p["pr_url"],
                p["source_priority"], p["observed_head"] or "ONBEKEND",
                p["observed_primary_gate"], p["last_seen_snapshot"],
                "JA, NIET bewezen gesloten" if p["missing_from_final_sample"]
                else "NEE", issue["human_next_action"], "ONBEKEND",
                "ONBEKEND", "ONBEKEND",
            ])
    for p in result["unlinked_prs"]:
        writer.writerow([
            "ONBEKEND", p["priority"], p["url"], p["priority"], "ONBEKEND",
            "ONBEKEND", p["last_seen_snapshot"],
            "ONBEKEND", "Eerst oorspronkelijke PR/issue-scope live bekijken.",
            "ONBEKEND", "ONBEKEND", "ONBEKEND",
        ])
    return "\ufeff" + source.getvalue()


def render(report, fmt):
    if fmt == "json":
        value = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    elif fmt == "markdown":
        value = markdown_content(report)
    elif fmt == "csv":
        value = csv_content(report)
    else:
        value = html_content(report)
    data = value.encode("utf-8")
    if not data or len(data) > MAX_BYTES:
        raise ValueError("Private issue-first handoff exceeds one MiB")
    return data


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, action="append", required=True,
                        help="Chronological private ZIP, 1..12 times")
    parser.add_argument("--priority", choices=PRIORITIES)
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--page-size", type=int, default=30)
    parser.add_argument("--format", choices=("html", "markdown", "csv", "json"),
                        default="html")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        source = build_timeline(args.source, minimum_sources=1)
        issue_report = issue_worklist(source)
        filtered = filter_issue_page(issue_report, priority=args.priority,
                                     page=args.page, page_size=args.page_size)
        data = render(filtered, args.format)
        if not hasattr(os, "O_NOFOLLOW"):
            raise ValueError("Safe local output requires nofollow")
        fd = os.open(args.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY |
                     os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            args.output.unlink(missing_ok=True)
            raise
        print("Private issue-first saved PR handoff: " +
              str(filtered["selection"]["shown_source_issues"]) + " issue(s) shown, " +
              str(issue_report["linked_issue_count"]) + " source-linked, " +
              str(issue_report["pr_without_source_issue_count"]) +
              " original PRs without an issue reference. No claim or merge implied.")
        return 0
    except (OSError, ValueError, TypeError, KeyError, UnicodeError,
            BadZipFile, OverflowError) as exc:
        print("Refused invalid/partial or oversized private source: " +
              type(exc).__name__, file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())

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
import re
import sys
from zipfile import BadZipFile

from zsentinel_saved_dossier_timeline import build_timeline, PRIORITIES

MAX_BYTES = 1024 * 1024
MAX_ISSUES = 600
RANK = {name: index for index, name in enumerate(PRIORITIES)}
ISSUE = re.compile(r"https://github[.]com/Zennay/[A-Za-z0-9_.-]{1,100}/issues/[1-9][0-9]{0,8}\Z")


def issue_worklist(report):
    """Group truly linked canonical issue addresses across sampled existing PRs.

    An issue absent from these PR snapshots is unknown, not fixed or free.
    The same issue may have several existing PRs and independent current owners.
    """
    indexed = {}
    unlinked = []
    for candidate in report["entries"]:
        # Track each issue independently. A PR may reference issue A in
        # one source snapshot and issue B in a later snapshot; attributing
        # the union of all snapshot numbers to both would be false evidence.
        issue_seen_at = {}
        for seen in candidate["observations"]:
            for url in seen["linked_issue_urls"]:
                issue_seen_at.setdefault(url, set()).add(seen["snapshot_index"])
        if not issue_seen_at:
            unlinked.append({
                "url": candidate["url"],
                "priority": candidate["latest_sample_priority"],
                "last_seen_snapshot": candidate["last_seen_snapshot"],
                "missing_from_final_sample": candidate["missing_from_final_snapshot"],
                "linked_issue_not_observed": True,
                "owner_claim_verified": False,
                "merge_verified": False,
            })
        for url, issue_snapshots in sorted(issue_seen_at.items()):
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
            issue["observed_in_snapshots"].update(issue_snapshots)
            issue["linked_existing_prs"].append({
                "issue_ref_observed_in_snapshots": sorted(issue_snapshots),
                "issue_ref_last_seen_snapshot": max(issue_snapshots),
                "issue_ref_absent_in_last_snapshot":
                    max(issue_snapshots) != len(report["source_snapshots"]),
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
        last_snapshot = len(report["source_snapshots"])
        issue["last_link_observed_snapshot"] = max(issue["observed_in_snapshots"])
        issue["link_unobserved_in_final_sample"] = (
            issue["last_link_observed_snapshot"] != last_snapshot)
        # Separate disappearing links from candidate PRs disappearing entirely.
        # Neither is evidence that the original issue was closed or delivered.
        issue["links_lost_while_pr_still_sampled"] = sum(
            p["issue_ref_absent_in_last_snapshot"]
            and not p["missing_from_final_sample"]
            for p in issue["linked_existing_prs"])
        issue["sampled_prs_missing_from_final"] = sum(
            p["missing_from_final_sample"]
            for p in issue["linked_existing_prs"])
        issue["multiple_candidate_prs_need_owner_review"] = (
            len(issue["linked_existing_prs"]) > 1)
        issue["last_snapshot_missing_batches"] = bool(
            report["source_snapshots"][-1]["missing_batch_indexes"])
        if issue["link_unobserved_in_final_sample"]:
            issue["triage_focus"] = (
                "Issue-PR verband ontbreekt in de laatste begrensde bron. "
                "Controleer bronvenster en oorspronkelijke issue/PR live; "
                "geen enkele sluiting, merge of claimvrijgave is bewezen.")
        elif issue["links_lost_while_pr_still_sampled"]:
            issue["triage_focus"] = (
                "Een eerder gekoppelde PR is nog bronwaargenomen maar de "
                "issuekoppeling niet. Controleer PR-omschrijving en actuele "
                "issue-relatie voordat je een bestaande reparatie afschrijft.")
        elif issue["multiple_candidate_prs_need_owner_review"]:
            issue["triage_focus"] = (
                "Meerdere bestaande PR-kandidaten bij hetzelfde issue. "
                "Controleer actuele file-scopes, claims en review; "
                "kies geen nieuwe parallelle implementatie zonder verificatie.")
        else:
            issue["triage_focus"] = (
                "Controleer de originele issue- en bestaande PR-status live "
                "voordat je een vervolgclaim of merge beoordeelt.")
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


def filter_issue_page(report, *, priority=None, issue_url=None,
                      page=1, page_size=30):
    if priority is not None and priority not in RANK:
        raise ValueError("Invalid source-PR priority")
    if issue_url is not None and (
        type(issue_url) is not str or ISSUE.fullmatch(issue_url) is None
        or priority is not None or page != 1
    ):
        raise ValueError("Invalid exact historical issue selection")
    if (type(page) is not int or not 1 <= page <= 100
            or type(page_size) is not int or not 1 <= page_size <= 100):
        raise ValueError("Invalid issue source page")
    all_rows = report["issues"]
    rows = [g for g in all_rows
            if (priority is None or g["highest_source_priority"] == priority)
            and (issue_url is None or g["issue_url"] == issue_url)]
    if issue_url is not None and len(rows) != 1:
        raise ValueError("Issue not linked to any PR in these saved sources")
    first = (page - 1) * page_size
    if first >= len(rows) and (rows or page != 1):
        raise ValueError("Issue page not present in source sample")
    display = dict(report)
    display["issues"] = rows[first:first + page_size]
    if issue_url is not None:
        # A targeted owner packet must not leak unrelated unsourced PR data.
        display["unlinked_prs"] = []
    display["selection"] = {
        "priority_filter": priority,
        "exact_source_issue_filter": issue_url,
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
                ' · issue-link bronmoment(en) ' +
                e(", ".join(map(str, p["issue_ref_observed_in_snapshots"]))) +
                (' · Issueverband niet meer in laatste bron (ONBEKEND)'
                 if p["issue_ref_absent_in_last_snapshot"] else '') +
                (' · Verdwenen uit eindsteekproef (NIET gesloten)'
                 if p["missing_from_final_sample"] else '') + '</li>')
        cards.append(
            '<article><h2><a target="_blank" rel="noopener noreferrer" href="' +
            e(issue["issue_url"]) + '">Issue #' + str(issue["issue_number"]) +
            '</a> · ' + e(issue["highest_source_priority"]) + '</h2><p>' +
            str(len(prs)) + ' bestaande PR-link(s) in opgeslagen bronmomenten: ' +
            e(", ".join(str(x) for x in issue["observed_in_snapshots"])) +
            '</p><p><strong>Geselecteerde menselijke volgende stap:</strong> ' +
            e(issue["triage_focus"]) + '</p><p>Bronwaarnemingen: ' +
            str(issue["sampled_prs_missing_from_final"]) +
            ' eerdere PR-kandidaten buiten laatste steekproef · ' +
            str(issue["links_lost_while_pr_still_sampled"]) +
            ' eerdere issue-links niet meer waargenomen bij nog bemonsterde PRs' +
            (' · laatste bronbatch onvolledig'
             if issue["last_snapshot_missing_batches"] else '') +
            ' (GEEN sluit- of mergebewijs).</p><ol>' +
            ''.join(prs) + '</ol><p>' +
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
('<details><summary>PRs zonder waargenomen bronissue (' +
str(result["pr_without_source_issue_count"]) +
')</summary><ol>' + loose +
'</ol><p>Dit kan een onvolledige GitHub-zoekscope zijn, geen bewijs van vrij werk.</p></details>'
 if selection["exact_source_issue_filter"] is None
 else '<p>Andere, ongekoppelde PRs niet opgenomen in dit gerichte issue-dossier.</p>') +
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
            "**Historische triage:** " + issue["triage_focus"], "",
            "- Issue-PR verband ontbreekt uit laatste bron: " +
            ("JA, NIET gesloten" if issue["link_unobserved_in_final_sample"] else "NEE"),
            "- Bron-PRs buiten eindsteekproef: " +
            str(issue["sampled_prs_missing_from_final"]),
            "- PRs met eerder issueverband dat niet meer in laatste bron voorkomt: " +
            str(issue["links_lost_while_pr_still_sampled"]),
            "- Ontbrekende batches in laatste bron: " +
            ("JA, selectie kan incompleet zijn"
             if issue["last_snapshot_missing_batches"] else
             "niet gerapporteerd (steekproef blijft beperkt)"), "",
            issue["human_next_action"], "",
        ])
        for p in issue["linked_existing_prs"]:
            lines.append("- [Bestaande PR #" + str(p["pr_number"]) + "](" +
                         p["pr_url"] + ") — " + p["source_priority"] +
                         " · oude bron-HEAD " +
                         (p["observed_head"] or "ONBEKEND") +
                         " · gate " + p["observed_primary_gate"] +
                         " · issue-link laatst gezien in bron " +
                         str(p["issue_ref_last_seen_snapshot"]) +
                         (" · issue-link niet meer gezien (ONBEKEND)"
                          if p["issue_ref_absent_in_last_snapshot"] else "") +
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
        "Laatst gezien bronnummer", "Issue-link laatst gezien",
        "Issue-link niet meer in eindsteekproef",
        "Verdwenen uit eindsteekproef",
        "Menselijke vervolgactie", "Huidige claim/CI/review",
        "Main merge", "Issue status live", "Historische issue triage",
        "Issuekoppeling ontbreekt eindbron", "Meerdere bron-PRs",
        "Laatste bron mist batches",
    ])
    for issue in result["issues"]:
        for p in issue["linked_existing_prs"]:
            writer.writerow([
                issue["issue_url"], issue["highest_source_priority"], p["pr_url"],
                p["source_priority"], p["observed_head"] or "ONBEKEND",
                p["observed_primary_gate"], p["last_seen_snapshot"],
                p["issue_ref_last_seen_snapshot"],
                "JA, issueverband ONBEKEND" if p["issue_ref_absent_in_last_snapshot"]
                else "NEE",
                "JA, NIET bewezen gesloten" if p["missing_from_final_sample"]
                else "NEE", issue["human_next_action"], "ONBEKEND",
                "ONBEKEND", "ONBEKEND",
                issue["triage_focus"],
                "JA, ONBEKEND" if issue["link_unobserved_in_final_sample"] else "NEE",
                "JA, verifieer eigenaren" if issue["multiple_candidate_prs_need_owner_review"] else "NEE",
                "JA, incompleet" if issue["last_snapshot_missing_batches"] else "niet gerapporteerd",
            ])
    for p in result["unlinked_prs"]:
        writer.writerow([
            "ONBEKEND", p["priority"], p["url"], p["priority"], "ONBEKEND",
            "ONBEKEND", p["last_seen_snapshot"],
            "ONBEKEND", "ONBEKEND",
            "ONBEKEND", "Eerst oorspronkelijke PR/issue-scope live bekijken.",
            "ONBEKEND", "ONBEKEND", "ONBEKEND",
            "Geen betrouwbaar bronissue; inspecteer actuele PR.", "ONBEKEND",
            "ONBEKEND", "ONBEKEND",
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
    parser.add_argument("--inspect-issue",
                        help="Exact canonical GitHub issue URL from saved sources; not live")
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--page-size", type=int, default=30)
    parser.add_argument("--format", choices=("html", "markdown", "csv", "json"),
                        default="html")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        source = build_timeline(args.source, minimum_sources=1)
        issue_report = issue_worklist(source)
        filtered = filter_issue_page(
            issue_report, priority=args.priority, issue_url=args.inspect_issue,
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

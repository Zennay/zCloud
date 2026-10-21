#!/usr/bin/env python3
"""Offline portfolio-wide issue-to-existing-PR continuity inbox.

Compares two owner-private, saved source-bound ZIP dossiers. Absence from
sample, a changed PR SHA or an old green CI count never proves a merge,
issue closure, released owner claim or live recovery. No GitHub/Notion writes.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from html import escape
from pathlib import Path
import sys
from zipfile import BadZipFile

from zsentinel_saved_issue_archive import read_issue_dossier
from zsentinel_saved_issue_delta import (
    admit_comparable_source_pair, issue_difference, save_private_html,
)
from zsentinel_saved_gate_dossier import PROJECT, _save_private_handoff
from zsentinel_saved_dossier_timeline import source_freshness

PRIORITIES = ("P0", "P1", "P2", "Unspecified")
RANK = {p: i for i, p in enumerate(PRIORITIES)}
MAX_ISSUES = 200
MAX_DISPLAY = 60
MAX_BYTES = 48 * 1024


def portfolio_delta(previous, current, *, stale_after_hours=6, evaluated_at=None):
    """A complete bounded inventory of original issue references, not status."""
    old_sha, new_sha = admit_comparable_source_pair(previous, current)
    _, _, old_receipt, old_issues, old_sources = previous
    _, _, new_receipt, new_issues, new_sources = current
    evaluated_at = evaluated_at or datetime.now(timezone.utc)
    old_freshness = source_freshness(
        [{"latest_observed_utc": old_sources[-1]["latest_observed_utc"]}],
        stale_after_hours=stale_after_hours, evaluated_at=evaluated_at)
    new_freshness = source_freshness(
        [{"latest_observed_utc": new_sources[-1]["latest_observed_utc"]}],
        stale_after_hours=stale_after_hours, evaluated_at=evaluated_at)
    old_map = {item["issue_url"]: item for item in old_issues}
    new_map = {item["issue_url"]: item for item in new_issues}
    urls = sorted(set(old_map) | set(new_map))
    if len(urls) > MAX_ISSUES:
        raise ValueError("Source issue inventory exceeds bounded portfolio view")
    entries = []
    counts = {"newly_sampled_issues": 0,
              "not_seen_in_newer_sample": 0,
              "changed_existing_issue_prs": 0,
              "unchanged_sampled_issues": 0}
    for url in urls:
        old, new = old_map.get(url), new_map.get(url)
        issue = new or old
        if issue["highest_source_priority"] not in RANK:
            raise ValueError("Unknown source issue priority")
        if new is None:
            kind = "not_seen"
            counts["not_seen_in_newer_sample"] += 1
            earlier = sorted(x["pr_url"] for x in old["linked_existing_prs"])
            added, missing, modified = [], earlier, []
            total_stable = 0
        else:
            delta = issue_difference(previous, current, url)
            added = delta["first_seen"]
            missing = delta["no_longer_seen"]
            modified = delta["changed"]
            total_stable = delta["stable"]
            if old is None:
                kind = "newly_sampled"
                counts["newly_sampled_issues"] += 1
            elif added or missing or modified or (
                    old["highest_source_priority"] !=
                    new["highest_source_priority"]):
                kind = "changed"
                counts["changed_existing_issue_prs"] += 1
            else:
                kind = "unchanged"
                counts["unchanged_sampled_issues"] += 1
        priority = min(
            (item["highest_source_priority"] for item in (old, new) if item),
            key=lambda value: RANK[value],
        )
        entries.append({
            "issue_url": url, "repository": issue["repository"],
            # Rank by highest historically observed issue-linked severity
            # across both snapshots; downgrades cannot bury an older P0.
            # This is not proof of current GitHub issue priority.
            "priority": priority,
            "old_priority": old["highest_source_priority"] if old else None,
            "new_priority": new["highest_source_priority"] if new else None,
            "kind": kind,
            "newly_sampled_prs": added,
            "not_seen_prs": missing,
            "changed_prs": modified,
            "unchanged_prs": total_stable,
            "last_sample_missing_batches": (
                len(new_sources[-1]["missing_batch_indexes"])),
            "current_issue_open_status_verified": False,
            "claim_verified": False, "merge_verified": False,
            "deploy_verified": False,
        })
    order = {"changed": 0, "newly_sampled": 1,
             "not_seen": 2, "unchanged": 3}
    entries.sort(key=lambda item: (
        RANK[item["priority"]], order[item["kind"]],
        -len(item["changed_prs"]), item["issue_url"]))
    return {
        "schema": "zsentinel-private-historical-issue-portfolio-delta-v1",
        "source": "two_validated_saved_private_issue_zip_sources_not_live",
        "old_manifest_sha256": old_sha,
        "new_manifest_sha256": new_sha,
        "old_latest_observed_utc": old_sources[-1]["latest_observed_utc"],
        "new_latest_observed_utc": new_sources[-1]["latest_observed_utc"],
        "old_source_freshness": old_freshness,
        "new_source_freshness": new_freshness,
        "old_sample_missing_batches":
            len(old_sources[-1]["missing_batch_indexes"]),
        "new_sample_missing_batches":
            len(new_sources[-1]["missing_batch_indexes"]),
        "old_sample_issues": len(old_issues),
        "new_sample_issues": len(new_issues),
        "total_distinct_source_issues": len(entries),
        "counts": counts, "issues": entries,
        "source_scope": old_receipt.get("scope"),
        "sample_absence_is_not_resolution": True,
        "worker_claim_authorized": False,
        "required_ci_review_verified": False,
        "merged_on_main_verified": False, "live_verified": False,
    }


def selected_page(report, *, priority=None, repository=None,
                  change_only=False, page=1, page_size=30):
    """Retain honest global counts while rendering only chosen rows."""
    if priority is not None and priority not in RANK:
        raise ValueError("Invalid priority filter")
    if repository is not None and (
            type(repository) is not str or PROJECT.fullmatch(repository) is None):
        raise ValueError("Invalid canonical GitHub project repository")
    if type(page) is not int or not 1 <= page <= 10:
        raise ValueError("Invalid page")
    if type(page_size) is not int or not 1 <= page_size <= MAX_DISPLAY:
        raise ValueError("Invalid page size")
    matches = [
        row for row in report["issues"]
        if (priority is None or row["priority"] == priority)
        and (repository is None or row["repository"] == repository)
        and (not change_only or row["kind"] != "unchanged")
    ]
    start = (page - 1) * page_size
    if (matches and start >= len(matches)) or (not matches and page != 1):
        raise ValueError("Page outside historical issue inventory")
    page_data = dict(report)
    page_data["issues"] = matches[start:start + page_size]
    page_data["display"] = {
        "page": page, "page_size": page_size,
        "total_pages": max(1, (len(matches) + page_size - 1) // page_size),
        "shown": len(page_data["issues"]),
        "matched": len(matches), "total": report["total_distinct_source_issues"],
        "priority": priority, "repository": repository,
        "change_only": change_only,
        "unshown_or_absent_is_never_closed": True,
    }
    return page_data


def render_markdown(report):
    s = report["display"]
    old_freshness = report["old_source_freshness"]
    new_freshness = report["new_source_freshness"]
    def age_text(item):
        return str(item["age_hours"]) + " uur" if item["age_hours"] is not None else "ONBEKEND"
    lines = [
        "# zSentinel · historisch portfolio-issue hersteloverzicht", "",
        "> ALLEEN PRIVÉ BRONSTEEKPROEVEN, NIET LIVE. Weggevallen items bewijzen "
        "geen issue-sluiting, PR-merge, vrije claims of herstel.", "",
        "- Oudere bron: " + report["old_latest_observed_utc"] +
        " · manifest SHA256 " + report["old_manifest_sha256"],
        "- Nieuwere bron: " + report["new_latest_observed_utc"] +
        " · manifest SHA256 " + report["new_manifest_sha256"],
        "- Actualiteit oud/nieuw: " + old_freshness["status"].upper() +
        " (" + age_text(old_freshness) + ") → " +
        new_freshness["status"].upper() + " (" + age_text(new_freshness) +
        "); drempel " + str(new_freshness["stale_after_hours"]) + " uur",
        "- Evaluatie UTC: " + new_freshness["evaluated_at_utc"] +
        " · beide bronnen zijn historisch; live hercontrole vóór actie verplicht",
        "- Ontbrekende bronbatches: " +
        str(report["old_sample_missing_batches"]) + " → " +
        str(report["new_sample_missing_batches"]),
        "- Historische projectfilter: " + (s["repository"] or "alle"), 
        "- Getoonde prioriteit is hoogste historisch gelinkte ernst; "
        "geen actuele GitHub-issue-labelstatus.",
        "- Totale historische issue-unie: " + str(s["total"]),
        "- Filtermatches: " + str(s["matched"]) + ", getoond: " +
        str(s["shown"]) + "; pagina " + str(s["page"]) + "/" +
        str(s["total_pages"]),
        "- Bronrelaties nieuw/verdwenen/gewijzigd/ongewijzigd: " +
        " / ".join(str(report["counts"][k]) for k in (
            "newly_sampled_issues", "not_seen_in_newer_sample",
            "changed_existing_issue_prs", "unchanged_sampled_issues")),
        "",
    ]
    labels = {
        "newly_sampled": "Nieuw in de bewaarde steekproef (NIET nieuw issue bewezen)",
        "not_seen": "Verdwenen uit bewaarde steekproef — NIET opgelost",
        "changed": "Brongebonden PR-relaties gewijzigd",
        "unchanged": "Geen verschil in de vergeleken broneigenschappen",
    }
    for row in report["issues"]:
        lines.extend([
            "## " + row["priority"] + " · [" + row["issue_url"] + "](" +
            row["issue_url"] + ")", "",
            "- Bronstatus: " + labels[row["kind"]],
            "- Eerder/later waargenomen issue-prioriteit: " +
            (row["old_priority"] or "NIET IN STEEKPROEF") + " → " +
            (row["new_priority"] or "NIET IN STEEKPROEF"),
        ])
        for field, label in (
            ("newly_sampled_prs", "Nieuw waargenomen bestaande PR-links"),
            ("not_seen_prs", "Niet meer waargenomen PR-links; NIET gemerged"),
        ):
            links = row[field]
            lines.append("- " + label + ": " + str(len(links)))
            for url in links[:5]:
                lines.append("  - " + url)
            if len(links) > 5:
                lines.append("  - " + str(len(links) - 5) +
                             " extra PR-bronlinks weggelaten; "
                             "NIET gesloten of vrij eigenaarschap")
        lines.append("- Gewijzigde bronwaarnemingen: " +
                     str(len(row["changed_prs"])))
        for url, reasons in row["changed_prs"][:5]:
            lines.append("  - " + url + ": " + ", ".join(reasons))
        if len(row["changed_prs"]) > 5:
            lines.append("  - " + str(len(row["changed_prs"]) - 5) +
                         " aanvullende gewijzigde PR-bronlinks weggelaten; "
                         "geen bewijs van herstel")
        lines.append("")
    lines.extend([
        "## Vervolg voor bevoegde operator", "",
        "Controleer originele issues en bestaande PRs live, inclusief huidige "
        "workerclaims, exact-head vereiste CI, onafhankelijke review en "
        "protected-main. Dit overzicht heeft geen merge-, deploy-, "
        "dispatch- of releasebevoegdheid.", "",
    ])
    value = "\n".join(lines)
    if not value or len(value.encode("utf-8")) > MAX_BYTES:
        raise ValueError("Private portfolio handoff exceeds size budget")
    return value


def render_html(report):
    e = lambda x: escape(str(x), quote=True)
    old_freshness = report["old_source_freshness"]
    new_freshness = report["new_source_freshness"]
    def age_text(item):
        return str(item["age_hours"]) + " uur" if item["age_hours"] is not None else "ONBEKEND"
    names = {
        "newly_sampled": "Nieuw waargenomen",
        "not_seen": "Niet meer waargenomen · NIET opgelost",
        "changed": "Bronwijziging",
        "unchanged": "Geen bronverschil",
    }
    cards = []
    for row in report["issues"]:
        items = []
        for name, label in (
            ("newly_sampled_prs", "Nieuw waargenomen PR-links"),
            ("not_seen_prs", "Bronlinks verdwenen, NIET afgerond"),
        ):
            links = row[name]
            contents = "".join(
                '<li><a href="' + e(url) +
                '" target="_blank" rel="noopener noreferrer">' +
                e(url) + "</a></li>" for url in links[:3]
            ) or "<li>Geen in dit bronvenster</li>"
            if len(links) > 3:
                contents += "<li>" + str(len(links) - 3) + (
                    " aanvullende bronlinks niet weergegeven. "
                    "Geen sluit-, merge- of claimvrijgavebewijs.</li>")
            items.append("<details><summary>" + label + ": " +
                         str(len(links)) + "</summary><ul>" +
                         contents + "</ul></details>")
        changed = "".join(
            '<li><a href="' + e(url) +
            '" target="_blank" rel="noopener noreferrer">' +
            e(url) + "</a> · " + e(", ".join(reasons)) + "</li>"
            for url, reasons in row["changed_prs"][:3]
        ) or "<li>Geen in dit bronvenster</li>"
        if len(row["changed_prs"]) > 3:
            changed += "<li>" + str(len(row["changed_prs"]) - 3) + (
                " overige gewijzigde bestaande PRs niet getoond; "
                "volledige bronselectie blijft onbekend.</li>")
        items.append("<details><summary>Gewijzigde PR-bronfeiten: " +
                     str(len(row["changed_prs"])) + "</summary><ul>" +
                     changed + "</ul></details>")
        cards.append(
            '<article><h2><a href="' + e(row["issue_url"]) +
            '" target="_blank" rel="noopener noreferrer">' +
            e(row["priority"] + " · " + row["issue_url"]) +
            '</a></h2><p><strong>' + e(names[row["kind"]]) +
            "</strong></p><p>Eerder/later bronprioriteit: " +
            e(row["old_priority"] or "onbekend") + " → " +
            e(row["new_priority"] or "onbekend") + "</p>" +
            "".join(items) + "</article>"
        )
    d = report["display"]
    content = """<!doctype html><html lang="nl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="referrer" content="no-referrer">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'none'; connect-src 'none'; img-src 'none'; style-src 'unsafe-inline'; form-action 'none'; base-uri 'none'">
<title>zSentinel · historische portfolio-issues</title>
<style>:root{color-scheme:dark;font-family:system-ui}
body{margin:0;background:#111f31;color:#eef5ff;line-height:1.6}
main{max-width:1150px;margin:auto;padding:clamp(16px,4vw,42px)}
aside,article{background:#1b314b;border:1px solid #587491;border-radius:12px;
padding:18px 22px;margin:16px 0}aside{border-color:#e9bf65}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,330px),1fr));gap:14px}
a{color:#99ddff;overflow-wrap:anywhere}a:focus-visible,summary:focus-visible{outline:3px solid #ffd779}
summary{cursor:pointer;padding:6px 0}li{margin:6px 0;overflow-wrap:anywhere}
article h2{font-size:1rem}code{overflow-wrap:anywhere;font-size:.85rem}
@media print{body,article,aside{background:white;color:black}}
</style></head><body><main><h1>Historische issue→PR herstel-inbox</h1>
<aside><strong>Niet live. Geen mergevrijgave.</strong> Alle rijen komen uit
privé opgeslagen, begrensde bronmomenten. Een verdwenen issue/PR kan buiten
de steekproef vallen; dit bewijst geen sluiting, vrijgegeven claim,
geslaagde CI/review, merge of deployment.</aside>""" + (
    "<p><strong>Bronnen:</strong> " + e(report["old_latest_observed_utc"]) +
    " → " + e(report["new_latest_observed_utc"]) +
    "<br>Manifest-SHA256: <code>" + e(report["old_manifest_sha256"]) +
    "</code> → <code>" + e(report["new_manifest_sha256"]) +
    "</code><br>Ontbrekende bronbatches: " +
    str(report["old_sample_missing_batches"]) + " → " +
    str(report["new_sample_missing_batches"]) +
    "</p><p><strong>Bronactualiteit oud/nieuw:</strong> " +
    e(old_freshness["status"].upper()) + " (" + e(age_text(old_freshness)) +
    ") → " + e(new_freshness["status"].upper()) + " (" +
    e(age_text(new_freshness)) + ") · drempel " +
    str(new_freshness["stale_after_hours"]) + " uur · evaluatie UTC " +
    e(new_freshness["evaluated_at_utc"]) +
    ". Recent betekent niet live; controleer actuele eigenaar/CI/merge.</p><p><strong>" + str(d["shown"]) + " getoond</strong> van " +
    str(d["matched"]) + " filtersmatches; " + str(d["total"]) +
    " unieke bronissues; pagina " + str(d["page"]) + "/" +
    str(d["total_pages"]) + ". Projectfilter: " +
    e(d["repository"] or "alle") +
    ". Prioriteit is de hoogste historische issuekoppeling, niet live." +
    " Niet getoond is NIET opgelost.</p>"
    ) + '<div class="grid">' + "".join(cards) + """</div>
<footer>Raadpleeg actuele GitHub-PR, review, exact-head CI, branch protection,
Notion-claim en live-bron alvorens bevoegde merge/release-actie.
Geen API-calls, schedulerwijzigingen of mutaties van andere systemen.
</footer></main></body></html>"""
    if len(content.encode("utf-8")) > MAX_BYTES:
        raise ValueError("Private portfolio HTML exceeds size budget")
    return content


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous", type=Path, required=True)
    parser.add_argument("--current", type=Path, required=True)
    parser.add_argument("--stale-after-hours", type=int, default=6,
                        help="Mark saved evidence stale after 1..168 hours (default: 6)")
    parser.add_argument("--priority", choices=PRIORITIES)
    parser.add_argument("--repository", help="Filter canonical Zennay/repo")
    parser.add_argument("--changed-only", action="store_true")
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--page-size", type=int, default=30)
    parser.add_argument("--format", choices=("html", "markdown"), default="html")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        earlier = read_issue_dossier(args.previous)
        later = read_issue_dossier(args.current)
        source = portfolio_delta(
            earlier, later, stale_after_hours=args.stale_after_hours)
        chosen = selected_page(
            source, priority=args.priority, repository=args.repository,
            change_only=args.changed_only,
            page=args.page, page_size=args.page_size)
        if args.format == "html":
            save_private_html(args.output, render_html(chosen))
        else:
            _save_private_handoff(args.output, render_markdown(chosen))
        print("Private historical issue-to-PR portfolio page created: " +
              str(chosen["display"]["shown"]) + " shown of " +
              str(chosen["display"]["total"]) + " issues")
        print("No live issue/owner/CI/review/main/deploy verified or authorized")
        return 0
    except (OSError, ValueError, TypeError, KeyError, UnicodeError,
            BadZipFile, OverflowError) as exc:
        print("Refused unsafe or incomparable private source: " +
              type(exc).__name__, file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())

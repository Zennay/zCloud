#!/usr/bin/env python3
"""zSentinel portfolio PR-conflict board: one command, up to 3 canonical projects.

This is a read-only inspection helper. It does not grant source ownership,
release rights, leases, CI status, or permission to edit another worker's work.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from html import escape
import json
import os
from pathlib import Path
import re
import sys

from zsentinel_pr_scope_guard import (
    ROOT, observe_conflict_graph, registry,
)

MAX_PROJECTS = 3
MAX_PRS_PER_PROJECT = 16
MAX_VISIBLE_PAIRS = 80


def collect_portfolio(projects, canonical, token=None, max_prs=10):
    if (not isinstance(projects, list)
            or not 1 <= len(projects) <= MAX_PROJECTS
            or len(set(projects)) != len(projects)
            or any(project not in canonical for project in projects)
            or type(max_prs) is not int
            or not 1 <= max_prs <= MAX_PRS_PER_PROJECT):
        raise ValueError("choose one to three distinct registered projects and 1-16 PRs")
    items = []
    for project in projects:
        repo = canonical[project]
        # A failure in one repository must NOT hide observed collisions in
        # another: fail closed for this source, continue independent projects.
        try:
            source = observe_conflict_graph(repo, token=token, limit=max_prs)
        except (ValueError, OSError):
            items.append({
                "project": project,
                "repository": repo,
                "source_status": "unavailable",
                "sample_complete": False,
                "shown_collision_pairs": [],
                "unpaired_open_prs_not_claim_verified": [],
                "observed_collision_pairs": None,
                "inspected_open_prs": None,
                "observed_open_prs": None,
                "source_observed_at_utc": None,
                "reason": "github_source_unavailable_or_incomplete",
            })
            continue
        pairs = source["shown_file_collision_pairs"][:MAX_VISIBLE_PAIRS]
        display_truncated = (source["collision_cards_truncated"] or
                             len(source["shown_file_collision_pairs"]) > MAX_VISIBLE_PAIRS)
        complete = source["pr_file_scope_sample_complete"] and not display_truncated
        items.append({
            "project": project,
            "repository": repo,
            "source_status": "complete_open_pr_sample_only"
                             if complete else "partial_open_pr_sample",
            "sample_complete": complete,
            "source_sample_complete": source["pr_file_scope_sample_complete"],
            "shown_collision_pairs": pairs,
            "unpaired_open_prs_not_claim_verified":
                source["unpaired_open_prs_in_sample_not_claim_verified"],
            "observed_collision_pairs": source["observed_file_collision_pairs"],
            "inspected_open_prs": source["inspected_prs"],
            "observed_open_prs": source["observed_open_prs"],
            "source_observed_at_utc": source["observed_at_utc"],
            "source_truncated": display_truncated,
            "reason": ("display_truncated_inspect_original_prs" if display_truncated
                       else "same_filename_does_not_prove_merge_conflict"),
        })
    return {
        "schema": "zsentinel-portfolio-pr-collision-board-v1",
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "projects": projects,
        "maximum_projects": MAX_PROJECTS,
        "maximum_prs_per_project": MAX_PRS_PER_PROJECT,
        "maximum_github_gets": len(projects) * (max_prs + 2),
        "source": "bounded_readonly_open_github_prs",
        "project_views": items,
        "source_coverage_complete": all(i["sample_complete"] for i in items),
        "any_file_overlaps_observed": any(
            bool(i["observed_collision_pairs"]) for i in items),
        "notion_claims_verified": False,
        "prless_branch_owners_verified": False,
        "worker_file_leases_verified": False,
        "file_edit_authorized": False,
        "claim_authorized": False,
        "merge_authorized": False,
        "deploy_authorized": False,
        "writes_performed": False,
    }


def summary_lines(view):
    if view["source_status"] == "unavailable":
        return ["Bron ONBESCHIKBAAR: geen oordeel over open PR's of overlap."]
    result = [
        "Open PR's bekeken: " + str(view["inspected_open_prs"]) +
        " / waargenomen " + str(view["observed_open_prs"]),
        "Waargenomen gedeelde-bestandsparen: " +
        str(view["observed_collision_pairs"]),
        "Bron: " + ("volledige huidige open PR-lijst (geen leasebewijs)"
                     if view["sample_complete"] else "ONVOLLEDIG: andere PR's kunnen ontbreken"),
    ]
    for pair in view["shown_collision_pairs"]:
        result.append(
            "  PR #" + str(pair["left"]["number"]) +
            " ↔ PR #" + str(pair["right"]["number"]) +
            " | " + str(pair["shared_file_count_observed"]) +
            " bestanden | " + pair["left"]["url"] +
            " | " + pair["right"]["url"])
    if view["unpaired_open_prs_not_claim_verified"]:
        result.append("Zonder gedeelde paden in steekproef (GEEN claimvrijgave):")
        for pr in view["unpaired_open_prs_not_claim_verified"]:
            result.append("  PR #" + str(pr["number"]) + " | " + pr["url"])
    if view.get("source_truncated"):
        result.append("  Details afgekapt; open bron-PR's voor verdere inspectie.")
    return result


def as_text(board):
    output = [
        "zSentinel | Portfolio PR-conflictbalie (alleen lezen)",
        "Projecten: " + ", ".join(board["projects"]),
    ]
    for view in board["project_views"]:
        output.append("")
        output.append("[" + view["project"] + "] " + view["repository"])
        output.extend(summary_lines(view))
    output.extend([
        "",
        "Gedeelde bestandsnamen ≠ bewezen Git-mergeconflict of huidige eigenaar.",
        "Notion-claims, PR-loze branches en workerleases zijn NIET geverifieerd.",
        "Geen automatische claim, code-edit, merge, runneringreep of deploy.",
    ])
    return "\n".join(output)


def as_markdown(board):
    lines = [
        "# zSentinel · portfolio PR-bestandsoverlap",
        "",
        "Bron: read-only, bounded GitHub open-PR steekproef, " +
        board["observed_at_utc"] + ".",
        "",
        "**Geen autorisatie:** Notion/workerclaims, PR-loze branches, " +
        "mergeconflicten en releasegates niet geverifieerd.",
    ]
    for view in board["project_views"]:
        lines.extend(["", "## " + view["project"], ""])
        if view["source_status"] == "unavailable":
            lines.append("Bron onbekend/niet beschikbaar. Geen vrijgave.")
            continue
        lines.extend([
            "Gelezen open PR's: " + str(view["inspected_open_prs"]) +
            " / " + str(view["observed_open_prs"]) +
            ". Compleet: " + ("ja (alleen open-PR-sample)" if view["sample_complete"]
                              else "nee") + ".",
            "",
        ])
        if not view["shown_collision_pairs"]:
            lines.append("Geen overlap in de gelezen steekproef; geen claimvrijgave.")
        for pair in view["shown_collision_pairs"]:
            left, right = pair["left"], pair["right"]
            lines.append("- [#" + str(left["number"]) + "](" + left["url"] +
                         ") ↔ [#" + str(right["number"]) + "](" + right["url"] +
                         "): " + str(pair["shared_file_count_observed"]) +
                         " gedeelde bestanden")
        if view["unpaired_open_prs_not_claim_verified"]:
            lines.extend(["", "Zonder gedeelde paden binnen de steekproef (GEEN claimvrijgave):"])
            for pr in view["unpaired_open_prs_not_claim_verified"]:
                lines.append("- [#" + str(pr["number"]) + "](" + pr["url"] + ")")
        if view.get("source_truncated"):
            lines.append("Bronkaarten afgekapt; inspecteer overige PR's.")
    return "\n".join(lines) + "\n"


def as_html(board):
    cards = []
    for view in board["project_views"]:
        rows = []
        if view["source_status"] != "unavailable":
            for pair in view["shown_collision_pairs"]:
                left, right = pair["left"], pair["right"]
                a, b = escape(left["url"], quote=True), escape(right["url"], quote=True)
                files = "<br>".join(
                    "<code>" + escape(p) + "</code>" for p in pair["shared_files"])
                rows.append(
                    "<tr><td><a href='" + a +
                    "' rel='noopener noreferrer'>#" + str(left["number"]) +
                    "</a> ↔ <a href='" + b +
                    "' rel='noopener noreferrer'>#" + str(right["number"]) +
                    "</a></td><td>" + str(pair["shared_file_count_observed"]) +
                    "</td><td>" + files + "</td></tr>")
        if not rows:
            if view["source_status"] == "unavailable":
                rows = ["<tr><td colspan='3'>BRON ONBESCHIKBAAR: er is geen "
                        "open-PR-inventaris gelezen en geen uitspraak over overlap mogelijk.</td></tr>"]
            else:
                rows = ["<tr><td colspan='3'>Geen overlap zichtbaar in de "
                        "gelezen steekproef; afwezigheid is geen vrijgave.</td></tr>"]
        state = ("Bron onbeschikbaar" if view["source_status"] == "unavailable"
                 else "bronoverlap > uitvoerlimiet; open PR's zelf voor overige botsingen"
                 if view.get("source_truncated")
                 else "volledige huidige open-PR-steekproef" if view["sample_complete"]
                 else "onvolledige open-PR-steekproef")
        unpaired = view["unpaired_open_prs_not_claim_verified"]
        links = "".join(
            "<li><a rel='noopener noreferrer' href='" +
            escape(pr["url"], quote=True) + "'>PR #" +
            str(pr["number"]) + "</a></li>" for pr in unpaired)
        cards.append(
            "<section><h2>" + escape(view["project"]) + "</h2>"
            "<p><strong>" + escape(state) +
            "</strong> · " + escape(view["repository"]) + "</p>"
            "<table><thead><tr><th>Open PR's</th><th>Gedeelde bestanden</th>"
            "<th>Bestandsnamen</th></tr></thead><tbody>" +
            "".join(rows) + "</tbody></table>"
            "<h3>Geen gedeelde paden in de steekproef</h3><p>Niet automatisch "
            "claimbaar; controleer nog steeds leases en PR-loze branches.</p><ul>" +
            (links or "<li>Geen andere PR's zichtbaar.</li>") +
            "</ul></section>")
    return (
        "<!doctype html><html lang='nl'><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>zSentinel · Portfolio PR-conflicten</title>"
        "<style>body{background:#111820;color:#e8eef8;font:16px system-ui;"
        "max-width:1050px;margin:auto;padding:20px}section{margin-top:32px}"
        "table{width:100%;border-collapse:collapse}td,th{border-bottom:1px solid "
        "#44566d;padding:10px;text-align:left;vertical-align:top;"
        "overflow-wrap:anywhere}a{color:#b7d7ff}"
        "aside{border-left:4px solid #9aa9b5;background:#263446;padding:1rem}"
        "code{overflow-wrap:anywhere}</style>"
        "<h1>zSentinel · Portfolio PR-conflicten</h1>"
        "<p>Projecten: " + str(len(board["project_views"])) +
        " · bronmoment: " + escape(board["observed_at_utc"]) + "</p>"
        "<aside>Alleen gedeelde bestandspaden uit een begrensde GitHub-bron. "
        "Dit bewijst geen mergeconflicten, claims of vrije werkscope. "
        "Geen automatische actie, merge of deploy.</aside>" +
        "".join(cards) + "</html>"
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=ROOT / "projects.json")
    parser.add_argument("--project", action="append", required=True)
    parser.add_argument("--max-prs", type=int, default=10)
    parser.add_argument("--format", choices=("text", "json", "html", "markdown"),
                        default="text")
    args = parser.parse_args(argv)
    try:
        canonical = registry(args.registry)
        board = collect_portfolio(args.project, canonical,
                                  os.environ.get("GITHUB_TOKEN"), args.max_prs)
        if args.format == "json":
            print(json.dumps(board, indent=2, ensure_ascii=False))
        elif args.format == "html":
            print(as_html(board))
        elif args.format == "markdown":
            print(as_markdown(board))
        else:
            print(as_text(board))
        return (2 if board["any_file_overlaps_observed"] else
                3 if not board["source_coverage_complete"] else 0)
    except (ValueError, OSError):
        print("zSentinel: portfolio PR source unavailable or invalid", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())

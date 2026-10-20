#!/usr/bin/env python3
"""Portfolio-wide human delivery view of previously saved zSentinel PR receipts.

This module never queries GitHub or Notion, changes claims, or authorizes CI,
reviews, merge, dispatch, production recovery or deployment.
"""
from __future__ import annotations

import csv
import json
from html import escape
from io import StringIO

from zsentinel_saved_gate_html import (
    _admit, _observed_gate, _classify, concurrent_gate_reasons, LABELS,
    PRIORITIES, PR,
)


def project_worklist(selected, integrated, *, project=None):
    """Compile one source-bound, P0-first human action per observed repository.

    Counts are bounded sample counts, NOT an inventory of all open PRs.
    A missing batch adds an unknown source case, never a zero gate.
    """
    _oldest, _latest, missing = _admit(selected, integrated)
    gates = _observed_gate(integrated, selected, missing)
    indexed = {}
    previous = set()
    for position, (priority, repo, number, ref) in enumerate(selected):
        url = ref["url"]
        if url in previous or not PR.fullmatch(url):
            raise ValueError("Duplicate or invalid portfolio PR")
        previous.add(url)
        # A canonical PR URL must belong to the claimed source repository.
        if url != "https://github.com/" + repo + "/pull/" + str(number):
            raise ValueError("Repository and PR identity disagree")
        gate = None if position // 4 + 1 in missing else gates.get(url)
        category = _classify(gate)
        concurrent = concurrent_gate_reasons(gate)
        item = indexed.setdefault(repo, {
            "repository": repo,
            "source_prs_observed": 0,
            "p0_prs_in_sample": 0,
            "p1_prs_in_sample": 0,
            "unknown_gate_source_prs": 0,
            "primary_categories": {key: 0 for key in LABELS},
            "concurrent_categories": {key: 0 for key in LABELS},
            "first_candidate": None,
            "candidates": [],
            "read_only": True,
            "ownership_verified": False,
            "merge_authorized": False,
            "live_verified": False,
        })
        item["source_prs_observed"] += 1
        if priority == 0:
            item["p0_prs_in_sample"] += 1
        if priority == 1:
            item["p1_prs_in_sample"] += 1
        if category == "source":
            item["unknown_gate_source_prs"] += 1
        item["primary_categories"][category] += 1
        for reason in concurrent:
            item["concurrent_categories"][reason] += 1
        candidate = {
            "priority": PRIORITIES[priority],
            "pr_number": number,
            "url": url,
            "primary_gate": category,
            "next_action": LABELS[category][1],
            "batch_index": position // 4 + 1,
            "gate_source": ("missing_batch" if position // 4 + 1 in missing
                            else "source_unavailable_or_head_changed" if category == "source"
                            else "observed_partial_gate_receipt"),
            "linked_issue_urls": sorted(issue["url"] for issue in ref["observed_issue_refs"]),
            "concurrent_gates": list(concurrent),
            # Keep only bounded, already validated saved gate facts. These
            # observations permit offline T0/T1 change detection but cannot
            # prove required checks, a review policy or main merge authority.
            "gate_observations": (
                {
                    "checks_completed": gate["observed_checks_completed"],
                    "checks_pending": gate["observed_checks_pending"],
                    "checks_not_successful": gate["observed_checks_not_successful"],
                    "independent_approvals_observed":
                        gate["observed_independent_exact_head_approvals"],
                    "changes_requested_observed":
                        gate["observed_exact_head_changes_requested"],
                    "legacy_pending_observed":
                        gate["legacy_status_pending_observed"],
                    "legacy_failed_observed":
                        gate["legacy_status_failure_or_error_observed"],
                    "checks_page_complete": gate["check_runs_page_complete"],
                    "reviews_page_may_be_truncated":
                        gate["review_page_may_be_truncated"],
                    "legacy_page_may_be_truncated":
                        gate["legacy_status_source_may_be_truncated"],
                }
                if gate is not None and category != "source" else None),
            "head_observed": (
                gate["head_sha_observed"]
                if gate is not None and category != "source" else None),
            "review_and_merge_authorized": False,
        }
        item["candidates"].append(candidate)
    if sum(p["source_prs_observed"] for p in indexed.values()) != len(selected):
        raise ValueError("Incomplete portfolio PR aggregation")
    for project in indexed.values():
        project["candidates"].sort(key=lambda c: (
            PRIORITIES.index(c["priority"]), list(LABELS).index(c["primary_gate"]),
            c["pr_number"]))
        project["first_candidate"] = project["candidates"][0]
        if len(project["candidates"]) != project["source_prs_observed"]:
            raise ValueError("Missing PR candidates in project worklist")
    ordered = sorted(indexed.values(), key=lambda p: (
        PRIORITIES.index(p["first_candidate"]["priority"]),
        -p["p0_prs_in_sample"], -p["p1_prs_in_sample"], p["repository"]))
    if project is not None:
        # Filtering happens only AFTER full original manifest/receipt admission.
        # A repo outside the bounded snapshot is UNKNOWN, never a free project.
        if type(project) is not str or project not in indexed:
            raise ValueError("Project not present in this source-bound PR sample")
        return [indexed[project]]
    return ordered


def portfolio_html(selected, integrated, *, project=None):
    """Offline accessible project-level prioritization with original PR links."""
    projects = project_worklist(selected, integrated, project=project)
    e = lambda value: escape(str(value), quote=True)
    cards = []
    for p in projects:
        first = p["first_candidate"]
        queue = []
        for candidate in p["candidates"]:
            issue_links = ' · '.join(
                '<a target="_blank" rel="noopener noreferrer" href="' +
                e(url) + '">' + e(url.rsplit("/", 1)[-1]) + '</a>'
                for url in candidate["linked_issue_urls"])
            combined = ", ".join(LABELS[reason][0]
                                 for reason in candidate["concurrent_gates"])
            observation = {
                "missing_batch": "Gate-batch ontbreekt",
                "source_unavailable_or_head_changed": "Bron/HEAD niet vastgepind",
                "observed_partial_gate_receipt": "Gedeeltelijke gatebron",
            }[candidate["gate_source"]]
            counters = candidate["gate_observations"]
            counter_detail = (
                " · Exact-HEAD steekproef: afgerond " +
                str(counters["checks_completed"]) + ", lopend " +
                str(counters["checks_pending"]) + ", niet succesvol " +
                str(counters["checks_not_successful"]) + ", onafhankelijke " +
                "approvals gezien " +
                str(counters["independent_approvals_observed"])
                if counters is not None else " · Actuele gatewaarneming UNKNOWN")
            queue.append(
                '<li><a href="' + e(candidate["url"]) +
                '" target="_blank" rel="noopener noreferrer">' +
                e(candidate["priority"]) + ' · #' +
                str(candidate["pr_number"]) + '</a> — ' +
                e(candidate["next_action"]) +
                '<br><small>' + e(observation) + ' · Batch ' +
                str(candidate["batch_index"]) + ' · ' + e(combined) +
                ' · Gekoppelde bronissues: ' + issue_links +
                e(counter_detail) + '</small></li>')
        counts = " · ".join(
            str(p["primary_categories"][key]) + " " + LABELS[key][0]
            for key in LABELS if p["primary_categories"][key])
        other = " · ".join(
            str(p["concurrent_categories"][key]) + " " + LABELS[key][0]
            for key in LABELS if p["concurrent_categories"][key])
        cards.append(
            '<article class="portfolio-project" id="project-' +
            e(p["repository"].replace("/", "-")) + '"><h3>' +
            e(p["repository"]) +
            '</h3><p>PR-kandidaten in bron: <strong>' +
            str(p["source_prs_observed"]) + '</strong> · P0: ' +
            str(p["p0_prs_in_sample"]) + ' · P1: ' +
            str(p["p1_prs_in_sample"]) + '</p>' +
            '<p>Bron onbekend: ' + str(p["unknown_gate_source_prs"]) +
            ' (niet opgelost of groen).</p><p>Hoofdblokkades: ' +
            e(counts) + '</p><details><summary>Alle gelijktijdige '
            'waarnemingen</summary><p>' + e(other) + '</p></details>' +
            '<p><strong>Volgende bestaande PR:</strong> <a href="' +
            e(first["url"]) +
            '" target="_blank" rel="noopener noreferrer">' +
            e(first["priority"]) + ' · #' + str(first["pr_number"]) +
            '</a> — ' + e(first["next_action"]) + '</p>' +
            '<details><summary>Alle ' + str(len(queue)) +
            ' bestaande PR-kandidaten en opvolgacties</summary>' +
            '<p>Alleen opgeslagen gedeeltelijke gatewaarnemingen; '
            'geen actuele vrijgave of eigendomsbewijs.</p><ol>' +
            ''.join(queue) + '</ol></details></article>')
    jump_links = '<nav aria-label="Spring direct naar project"><strong>' + \
        'Ga naar project: </strong>' + ' · '.join(
            '<a href="#project-' + e(p["repository"].replace("/", "-")) +
            '">' + e(p["repository"]) + ' (' +
            str(p["source_prs_observed"]) + ' PRs)</a>'
            for p in projects) + '</nav>'
    return ('<section class="portfolio-overview" aria-label="zSentinel '
            'portfolio afhandeloverzicht"><h2>Portfolio · bestaande PR '
            'afhandeling</h2><p>Alle aantallen zijn <strong>uitsluitend '
            'uit het opgeslagen bronvenster</strong>. Projecten zonder '
            'waargenomen PR kunnen buiten de steekproef liggen. Geen '
            'eigenaar, merge of live herstel is geverifieerd.</p>' +
            jump_links + '<div class="portfolio-grid">' +
            ''.join(cards) + '</div></section>')




def portfolio_page_html(selected, integrated, *, project=None):
    """One complete, fully offline project delivery page from saved receipts.

    The selected project must occur in the fully validated source; no new
    GitHub request, ownership claim, CI decision or worker dispatch is made.
    """
    oldest, latest, missing = _admit(selected, integrated)
    projects = project_worklist(selected, integrated, project=project)
    scoped = sum(p["source_prs_observed"] for p in projects)
    e = lambda value: escape(str(value), quote=True)
    source_digest = e(integrated["original_review_manifest_sha256"])
    board_digest = e(integrated["original_board_sha256"])
    label = e(project) if project is not None else "Geobserveerde portfolio"
    cards = portfolio_html(selected, integrated, project=project)
    html = """<!doctype html>
<html lang="nl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy"
 content="default-src 'none'; style-src 'unsafe-inline'; img-src 'none'; connect-src 'none'; form-action 'none'; base-uri 'none'; object-src 'none'">
<title>zSentinel — offline project PR-dossier</title>
<style>
:root{color-scheme:dark;font-family:system-ui,-apple-system,sans-serif}
*{box-sizing:border-box}body{margin:0;background:#111e30;color:#f2f8ff;line-height:1.55}
main{max-width:1150px;margin:auto;padding:clamp(15px,4vw,38px)}
h1{font-size:clamp(1.7rem,4vw,2.6rem);line-height:1.2}
a{color:#94d8ff;text-underline-offset:3px}
a:focus-visible,summary:focus-visible{outline:3px solid #fbd675;outline-offset:4px}
aside{border-left:4px solid #e9bb61;background:#2b2930;padding:14px 20px;border-radius:8px}
.provenance{margin:22px 0;padding:15px;background:#172b41;border-radius:10px;overflow-wrap:anywhere}
.provenance code{overflow-wrap:anywhere}
.portfolio-overview{margin-top:22px}
.portfolio-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,390px),1fr));gap:16px}
.portfolio-project{border:1px solid #607e92;background:#1b334c;padding:18px;border-radius:12px}
.portfolio-project h3{font-size:1.1rem;margin:0 0 8px}
.portfolio-project ol{padding-left:24px}
.portfolio-project li{margin:12px 0;overflow-wrap:anywhere}
.portfolio-project summary{cursor:pointer;padding:7px 2px;font-weight:650}
.portfolio-project small{color:#d3e1ed}
.portfolio-overview nav{margin:14px 0;padding:12px;background:#1b334c;border-radius:8px}
.portfolio-overview nav a{display:inline-block;margin:3px 7px}
article:target{outline:3px solid #fbd675;scroll-margin:20px}
footer{font-size:.9rem}
@media print{body{background:white;color:black}
aside,.portfolio-project,.provenance{background:white;color:black;border-color:#444}
a{color:#034f80}details{display:block}}
</style></head><body><main>
<header><p>zCloud / zSentinel / opgeslagen bronbewijs</p>
<h1>Projectdossier · """ + label + """</h1>
<p>Een bestaande PR-werkrij vanuit één strikt begrensde, privé opgeslagen
GitHub-observatie. Selectie en links zijn geen actueel releasebewijs.</p>
</header>
<aside><strong>Geen automatische uitvoering of mergevrijgave.</strong>
Oorspronkelijke CI, reviewers, actuele claims, main-branch-regels en
productie-autorisatie moeten afzonderlijk live gecontroleerd worden.
Niet waargenomen PRs of projecten zijn UNKNOWN, nooit opgelost of vrij.</aside>
<section class="provenance" aria-label="Herkomst">
<h2>Vastgepinde bron</h2>
<p>Reviewmanifest SHA256: <code>""" + source_digest + """</code><br>
Oorspronkelijk board SHA256: <code>""" + board_digest + """</code></p>
<p>Broninterval UTC: """ + e(oldest.isoformat()) + " — " + e(latest.isoformat()) + """<br>
Batches: """ + str(integrated["observed_batch_count"]) + "/" + str(integrated["expected_batch_count"]) + """.
Niet waargenomen batches: """ + e(", ".join(map(str, sorted(missing))) or "geen") + """.
Getoonde PRs: """ + str(scoped) + " van " + str(len(selected)) + """ uit
de oorspronkelijke begrensde PR-bron.</p>
</section>
""" + cards + """
<footer><p>Dit bestand blijft lokaal, gebruikt geen scripts, externe
lettertypes, API's, persistent browsergeheugen of automatische verzending.
Alleen vrijwillig openen van de originele GitHub-links navigeert extern.</p>
</footer></main></body></html>"""
    if len(html.encode("utf-8")) > 1024 * 1024:
        raise ValueError("Offline project dossier exceeds one MiB")
    return html


def portfolio_csv(selected, integrated, *, project=None):
    """One compact Excel-friendly human triage row per observed project."""
    projects = project_worklist(selected, integrated, project=project)
    stream = StringIO(newline="")
    writer = csv.writer(stream, delimiter=";", quotechar='"',
                        lineterminator="\r\n")
    writer.writerow([
        "Repository", "Oudste bron UTC", "Manifest SHA256",
        "PR kandidaten bron", "P0 kandidaten bron", "P1 kandidaten bron",
        "PR gatebron onbekend", "Hoofdblokkades bron",
        "Gelijktijdige blokkades bron", "Eerste PR prioriteit",
        "Eerste PR URL", "Vervolgactie mens",
        "Overige kandidaten bron", "Eerste drie PR URLs bron",
        "Verplichte CI/review bevestigd", "Huidige eigenaar geverifieerd",
        "Main merge bewezen", "Live geverifieerd",
    ])
    for p in projects:
        first = p["first_candidate"]
        labels = lambda key: " | ".join(
            str(p[key][name]) + " " + LABELS[name][0]
            for name in LABELS if p[key][name])
        writer.writerow([
            p["repository"], integrated["earliest_observed_at_utc"],
            integrated["original_review_manifest_sha256"],
            p["source_prs_observed"], p["p0_prs_in_sample"],
            p["p1_prs_in_sample"], p["unknown_gate_source_prs"],
            labels("primary_categories"), labels("concurrent_categories"),
            first["priority"], first["url"], first["next_action"],
            len(p["candidates"]) - 1,
            " | ".join(candidate["url"] for candidate in p["candidates"][:3]),
            "ONBEKEND", "ONBEKEND", "ONBEKEND", "ONBEKEND",
        ])
    result = "\ufeff" + stream.getvalue()
    if len(result.encode("utf-8")) > 1024 * 1024:
        raise ValueError("Portfolio handoff over one MiB")
    return result





def portfolio_pr_csv(selected, integrated, *, project=None):
    """One complete, sorted row per already-observed PR (not one per project).

    A project filter is admissible only after validating the full source.
    No free-text notes or raw GitHub responses can become CSV formulas.
    """
    projects = project_worklist(selected, integrated, project=project)
    stream = StringIO(newline="")
    writer = csv.writer(stream, delimiter=";", quotechar='"',
                        lineterminator="\r\n")
    writer.writerow([
        "Oorspronkelijk manifest SHA256", "Oorspronkelijk board SHA256",
        "Bron oudste UTC", "Bron nieuwste UTC", "Project",
        "Prioriteit", "Bestaande PR", "Originele PR URL",
        "Gekoppelde issue URLs", "Bronbatch", "Gatebron",
        "Hoofdblokkade", "Gelijktijdige blokkades", "Waargenomen HEAD SHA",
        "Menselijke vervolgactie", "Huidige eigenaar en mainwriter",
        "Exact-head vereiste CI en review", "Gemerged op main", "Live herstel",
    ])
    for item in projects:
        for candidate in item["candidates"]:
            writer.writerow([
                integrated["original_review_manifest_sha256"],
                integrated["original_board_sha256"],
                integrated["earliest_observed_at_utc"],
                integrated["latest_observed_at_utc"],
                item["repository"], candidate["priority"],
                candidate["pr_number"], candidate["url"],
                " | ".join(candidate["linked_issue_urls"]),
                candidate["batch_index"], candidate["gate_source"],
                LABELS[candidate["primary_gate"]][0],
                " | ".join(LABELS[key][0] for key in candidate["concurrent_gates"]),
                candidate["head_observed"] or "ONBEKEND",
                candidate["next_action"], "ONBEKEND", "ONBEKEND",
                "ONBEKEND", "ONBEKEND",
            ])
    result = "\ufeff" + stream.getvalue()
    if len(result.encode("utf-8")) > 1024 * 1024:
        raise ValueError("Private per-PR portfolio CSV exceeds one MiB")
    return result


def portfolio_markdown(selected, integrated, *, project=None):
    """Compact private project-by-project PR queue, without granting ownership.

    Unlike the full saved-gate review, this handoff keeps every observed PR
    next to its own project and original linked issues. A missing source is an
    explicit follow-up, never a successful or free PR.
    """
    projects = project_worklist(selected, integrated, project=project)
    lines = [
        "# zSentinel — projecten en bestaande PR-opvolging",
        "",
        "> Privé, offline momentopname. Niet live; geen vrijgave, eigenaar, "
        "verplichte CI, review, merge of deploy geverifieerd.",
        "",
        "- Reviewmanifest SHA256: `" +
        integrated["original_review_manifest_sha256"] + "`",
        "- Bronboard SHA256: `" + integrated["original_board_sha256"] + "`",
        "- Oudste bron UTC: " + integrated["earliest_observed_at_utc"],
        "- Nieuwste bron UTC: " + integrated["latest_observed_at_utc"],
        "- Waargenomen batches: " + str(integrated["observed_batch_count"]) +
        "/" + str(integrated["expected_batch_count"]),
        "- Ontbrekende batches (UNKNOWN): " +
        (", ".join(map(str, sorted(integrated["missing_batch_indexes"]))) or "geen"),
        "- Scope: " + (project or "alle waargenomen projecten") +
        " (bronselectie, niet gehele GitHub-inventaris)",
        "- Projecten getoond: " + str(len(projects)) +
        "; PRs oorspronkelijke bron: " + str(len(selected)) +
        "; PRs getoond: " +
        str(sum(p["source_prs_observed"] for p in projects)),
        "",
    ]
    status_labels = {
        "missing_batch": "batch ontbreekt",
        "source_unavailable_or_head_changed": "bron/HEAD onbekend",
        "observed_partial_gate_receipt": "gedeeltelijke gatebron",
    }
    for project in projects:
        lines.extend([
            "## " + project["repository"] + " — " +
            str(project["source_prs_observed"]) + " PR-kandidaten",
            "",
            "Bron P0: " + str(project["p0_prs_in_sample"]) +
            " · P1: " + str(project["p1_prs_in_sample"]) +
            " · onbekende gatebron: " +
            str(project["unknown_gate_source_prs"]),
            "",
        ])
        for candidate in project["candidates"]:
            refs = ", ".join(
                "[#" + url.rsplit("/", 1)[-1] + "](" + url + ")"
                for url in candidate["linked_issue_urls"])
            blockers = ", ".join(LABELS[key][0]
                                  for key in candidate["concurrent_gates"])
            lines.extend([
                "- **" + candidate["priority"] + "** " +
                "[PR #" + str(candidate["pr_number"]) + "](" +
                candidate["url"] + ") — " +
                LABELS[candidate["primary_gate"]][0],
                "  - Menselijke stap: " + candidate["next_action"],
                "  - Gelijktijdige signalen: " + blockers,
                "  - Batch " + str(candidate["batch_index"]) +
                ": " + status_labels[candidate["gate_source"]],
                "  - Exact-HEAD CI steekproef: " +
                (("afgerond " + str(candidate["gate_observations"]["checks_completed"]) +
                  ", lopend " + str(candidate["gate_observations"]["checks_pending"]) +
                  ", niet succesvol " +
                  str(candidate["gate_observations"]["checks_not_successful"]) +
                  ", onafhankelijke approvals gezien " +
                  str(candidate["gate_observations"]["independent_approvals_observed"]))
                 if candidate["gate_observations"] is not None else "ONBEKEND"),
                "  - Gekoppelde bronissues: " + (refs or "geen in bron"),
                "  - Huidige HEAD/reviews/main/live: opnieuw live verifiëren; "
                "geen vrijgave uit deze momentopname.",
            ])
        lines.append("")
    lines.extend([
        "## Verplichte menselijke vervolgstap",
        "",
        "Controleer oorspronkelijke GitHub issues en exacte huidige PR HEAD, "
        "actieve claims en bestandscope, verplichte CI, onafhankelijke "
        "review en beschermde mainregels. Alleen echte geautoriseerde "
        "GitHub main-merge en afzonderlijke live-verificatie zijn levering.",
        "Onzichtbare projecten/PR's in de bron zijn UNKNOWN, nooit vrij of afgerond.",
        "",
    ])
    result = "\n".join(lines)
    if len(result.encode("utf-8")) > 1024 * 1024:
        raise ValueError("Private portfolio Markdown exceeds one MiB")
    return result


def portfolio_json(selected, integrated, *, project=None):
    """Offline source-bound owner suggestions, never a worker-dispatch contract.

    Clients MUST verify fresh actual owner leases, protected-main policy and
    exact current PR HEADs independently before any authorized action. No
    statement in this report can turn unknown coverage into availability.
    """
    projects = project_worklist(selected, integrated, project=project)
    source = {
        "schema": "zsentinel-private-portfolio-work-suggestions-v1",
        "source": "saved_exact_manifest_gate_batches_not_live",
        "original_review_manifest_sha256": integrated["original_review_manifest_sha256"],
        "original_board_sha256": integrated["original_board_sha256"],
        "oldest_observed_at_utc": integrated["earliest_observed_at_utc"],
        "latest_observed_at_utc": integrated["latest_observed_at_utc"],
        "observed_batch_count": integrated["observed_batch_count"],
        "expected_batch_count": integrated["expected_batch_count"],
        "missing_batch_indexes": sorted(integrated["missing_batch_indexes"]),
        "known_complete_project_or_pr_inventory": False,
        "selected_project_filter": project,
        "original_source_pr_candidates": len(selected),
        "filtered_pr_candidates": sum(p["source_prs_observed"] for p in projects),
        "owner_claim_verified": False,
        "required_exact_head_ci_verified": False,
        "independent_reviews_and_branch_rules_verified": False,
        "worker_dispatch_authorized": False,
        "queue_mutation_authorized": False,
        "merge_authorized": False,
        "deploy_authorized": False,
        "worker_recovery_verified": False,
        "writes_performed": False,
        "projects": projects,
    }
    content = json.dumps(source, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if len(content.encode("utf-8")) > 1024 * 1024:
        raise ValueError("Private project evidence JSON exceeds one MiB")
    return content

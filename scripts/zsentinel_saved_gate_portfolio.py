#!/usr/bin/env python3
"""Portfolio-wide human delivery view of previously saved zSentinel PR receipts.

This module never queries GitHub or Notion, changes claims, or authorizes CI,
reviews, merge, dispatch, production recovery or deployment.
"""
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
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


def source_age_warning(latest):
    """Display the saved source age at export, never infer live coverage."""
    seconds = max(0, (datetime.now(timezone.utc) - latest).total_seconds())
    hours = round(seconds / 3600, 1)
    if seconds >= 6 * 3600:
        return "VEROUDERDE BRON: " + str(hours) + " uur oud bij rapportgeneratie. Controleer GitHub opnieuw."
    return "Bron " + str(hours) + " uur oud bij rapportgeneratie. GEEN live CI-, claim- of mergebewijs."


def source_gate_detail(candidate):
    """Human-readable completeness of one SAVED exact-head observation.

    These are evidence markers, never branch-protection or merge approval.
    """
    gate = candidate["gate_observations"]
    if gate is None:
        return "HEAD ONBEKEND · gatebron afwezig of gewijzigd"
    flags = []
    if gate["checks_page_complete"]:
        flags.append("checks-pagina volledig gezien")
    else:
        flags.append("checks-pagina ONVOLLEDIG")
    if gate["reviews_page_may_be_truncated"]:
        flags.append("reviewpagina mogelijk afgekapt")
    else:
        flags.append("reviewpagina niet als afgekapt gemarkeerd")
    if gate["legacy_page_may_be_truncated"]:
        flags.append("legacy statusbron mogelijk afgekapt")
    if gate["changes_requested_observed"]:
        flags.append(str(gate["changes_requested_observed"]) + " wijzigingsverzoeken gezien")
    if gate["legacy_failed_observed"]:
        flags.append("legacy checkfout gezien")
    if gate["legacy_pending_observed"]:
        flags.append("legacy check loopt")
    return " · ".join(flags) + " · bronwaarneming is GEEN mergevrijgave"


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
            head = candidate["head_observed"]
            head_receipt = (
                '<a href="https://github.com/' + e(p["repository"]) +
                '/commit/' + head + '" target="_blank" ' +
                'rel="noopener noreferrer">Bron HEAD <code>' +
                head[:12] + '</code></a>'
                if head is not None else '<strong>Bron HEAD ONBEKEND</strong>')
            receipt_completeness = source_gate_detail(candidate)
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
                e(counter_detail) + '<br>' + head_receipt +
                ' · ' + e(receipt_completeness) + '</small></li>')
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
            '</a> — ' + e(first["next_action"]) +
            '<br>Bron HEAD: <strong>' +
            e(first["head_observed"][:12] if first["head_observed"] else
              "ONBEKEND") + '</strong> · ' +
            e(source_gate_detail(first)) + '</p>' +
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





def portfolio_issue_html(selected, integrated, *, project=None):
    """Offline merge-first issue index from already validated saved PR evidence."""
    projects = project_worklist(selected, integrated, project=project)
    index = {}
    for group in projects:
        repo = group["repository"]
        for pr in group["candidates"]:
            for issue_url in pr["linked_issue_urls"]:
                prefix = "https://github.com/" + repo + "/issues/"
                number = issue_url[len(prefix):] if issue_url.startswith(prefix) else ""
                if (not number.isascii() or not number.isdecimal()
                        or not 1 <= len(number) <= 9 or number[0] == "0"):
                    raise ValueError("Invalid source-linked issue URL")
                entry = index.setdefault(issue_url, {"repo": repo, "prs": []})
                if entry["repo"] != repo:
                    raise ValueError("Conflicting issue source repository")
                entry["prs"].append(pr)
    rank = {priority: n for n, priority in enumerate(PRIORITIES)}
    ordered = sorted(index.items(), key=lambda pair: (
        min(rank[pr["priority"]] for pr in pair[1]["prs"]),
        pair[1]["repo"], int(pair[0].rsplit("/", 1)[-1])))
    e = lambda value: escape(str(value), quote=True)
    cards = []
    for url, info in ordered[:60]:
        prs = info["prs"]
        highest = min(prs, key=lambda pr: rank[pr["priority"]])
        links = []
        for pr in prs[:4]:
            observed_head = pr["head_observed"]
            head_label = (
                '<a href="https://github.com/' + e(info["repo"]) +
                '/commit/' + observed_head + '" target="_blank" ' +
                'rel="noopener noreferrer">Bron HEAD <code>' +
                observed_head[:12] + '</code></a>'
                if observed_head else '<strong>Bron HEAD ONBEKEND</strong>')
            links.append(
                '<li><a href="' + e(pr["url"]) + '" target="_blank" '
                'rel="noopener noreferrer">' + e(pr["priority"]) +
                ' · PR #' + str(pr["pr_number"]) + '</a> · ' +
                e(LABELS[pr["primary_gate"]][0]) + '<br>' +
                head_label + ' · ' + e(source_gate_detail(pr)) +
                '</li>')
        remaining = len(prs) - len(links)
        cards.append(
            '<li><details><summary>' + e(highest["priority"]) +
            ' · <a href="' + e(url) +
            '" target="_blank" rel="noopener noreferrer">' +
            e(info["repo"]) + ' issue #' + e(url.rsplit("/", 1)[-1]) +
            '</a> · ' + str(len(prs)) + ' PR-kandidaten</summary><ol>' +
            ''.join(links) + '</ol>' +
            ('<p>' + str(remaining) + ' andere PRs NIET beoordeeld</p>'
             if remaining else '') + '</details></li>')
    hidden = len(ordered) - len(cards)
    return (
        '<section class="portfolio-overview" aria-label="Issuegerichte PR-inbox">'
        '<h2>Issue naar bestaande PR · merge eerst</h2>'
        '<p>Historische, begrensde bronkoppelingen, niet de actuele GitHub-status. '
        'Controleer huidige eigenaar/claim, HEAD, verplichte CI, review, main en '
        'live release zelfstandig. Geen oude PR is automatisch vrij.</p>'
        '<p>Waargenomen issuekoppelingen: ' + str(len(ordered)) +
        ' · zichtbaar: ' + str(len(cards)) +
        ' · actuele status: <strong>ONBEKEND</strong>.</p>'
        '<ol class="issue-pr-index">' + ''.join(cards) + '</ol>' +
        ('<p>' + str(hidden) + ' niet getoonde bronissues; NIET opgelost.</p>'
         if hidden else '') +
        '</section>'
    )


def portfolio_page_html(selected, integrated, *, project=None):
    """One complete, fully offline project delivery page from saved receipts.

    The selected project must occur in the fully validated source; no new
    GitHub request, ownership claim, CI decision or worker dispatch is made.
    """
    oldest, latest, missing = _admit(selected, integrated)
    projects = project_worklist(selected, integrated, project=project)
    age_warning = source_age_warning(latest)
    scoped = sum(p["source_prs_observed"] for p in projects)
    e = lambda value: escape(str(value), quote=True)
    source_digest = e(integrated["original_review_manifest_sha256"])
    board_digest = e(integrated["original_board_sha256"])
    label = e(project) if project is not None else "Geobserveerde portfolio"
    cards = (portfolio_issue_html(selected, integrated, project=project) +
             portfolio_html(selected, integrated, project=project))
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
.issue-pr-index{padding-left:24px}
.issue-pr-index li{margin:9px 0;overflow-wrap:anywhere}
.issue-pr-index details{background:#192e45;border:1px solid #56768b;border-radius:9px;padding:10px}
.issue-pr-index summary{cursor:pointer}
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
<p><strong>""" + e(age_warning) + """</strong></p>
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
    _oldest, latest, _missing = _admit(selected, integrated)
    projects = project_worklist(selected, integrated, project=project)
    stream = StringIO(newline="")
    writer = csv.writer(stream, delimiter=";", quotechar='"',
                        lineterminator="\r\n")
    writer.writerow([
        "Repository", "Oudste bron UTC", "Manifest SHA256",
        "Bronleeftijd bij export",
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
            source_age_warning(latest), p["source_prs_observed"], p["p0_prs_in_sample"],
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
    _oldest, latest, _missing = _admit(selected, integrated)
    projects = project_worklist(selected, integrated, project=project)
    stream = StringIO(newline="")
    writer = csv.writer(stream, delimiter=";", quotechar='"',
                        lineterminator="\r\n")
    writer.writerow([
        "Oorspronkelijk manifest SHA256", "Oorspronkelijk board SHA256",
        "Bron oudste UTC", "Bron nieuwste UTC", "Bronleeftijd bij export", "Project",
        "Prioriteit", "Bestaande PR", "Originele PR URL",
        "Gekoppelde issue URLs", "Bronbatch", "Gatebron",
        "Hoofdblokkade", "Gelijktijdige blokkades", "Waargenomen HEAD SHA",
        "Menselijke vervolgactie", "Bron gate integriteit",
        "Huidige eigenaar en mainwriter",
        "Exact-head vereiste CI en review", "Gemerged op main", "Live herstel",
    ])
    for item in projects:
        for candidate in item["candidates"]:
            writer.writerow([
                integrated["original_review_manifest_sha256"],
                integrated["original_board_sha256"],
                integrated["earliest_observed_at_utc"],
                integrated["latest_observed_at_utc"],
                source_age_warning(latest), item["repository"], candidate["priority"],
                candidate["pr_number"], candidate["url"],
                " | ".join(candidate["linked_issue_urls"]),
                candidate["batch_index"], candidate["gate_source"],
                LABELS[candidate["primary_gate"]][0],
                " | ".join(LABELS[key][0] for key in candidate["concurrent_gates"]),
                candidate["head_observed"] or "ONBEKEND",
                candidate["next_action"], source_gate_detail(candidate),
                "ONBEKEND", "ONBEKEND", "ONBEKEND", "ONBEKEND",
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
    _oldest, latest, _missing = _admit(selected, integrated)
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
        "- Bronleeftijd bij export: " + source_age_warning(latest),
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
                "  - Waargenomen bron HEAD: " +
                (candidate["head_observed"] or "ONBEKEND"),
                "  - Bronintegriteit: " + source_gate_detail(candidate),
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


def single_pr_related_peers(selected, integrated, *, pr_url):
    """Cross-reference OTHER observed PRs touching the same saved issue refs.

    Complete original saved-source admission precedes all selection. This is
    bounded issue-link overlap in the sampled source, NOT proof of file
    collisions, current ownership, live open state or equivalent fixes.
    """
    if type(pr_url) is not str or PR.fullmatch(pr_url) is None:
        raise ValueError("Expected canonical GitHub pull-request URL")
    groups = project_worklist(selected, integrated)
    hits = [(group, item) for group in groups
            for item in group["candidates"] if item["url"] == pr_url]
    if len(hits) != 1:
        raise ValueError("Selected PR is not unique in admitted saved source")
    owner, target = hits[0]
    peers = {}
    for issue in target["linked_issue_urls"]:
        related = []
        for item in owner["candidates"]:
            if item["url"] == pr_url or issue not in item["linked_issue_urls"]:
                continue
            related.append({
                "url": item["url"],
                "head_observed": item["head_observed"],
                "priority": item["priority"],
                "gate_source": item["gate_source"],
                "primary_gate": item["primary_gate"],
                "review_and_merge_authorized": False,
            })
        related.sort(key=lambda row: int(row["url"].rsplit("/", 1)[-1]))
        peers[issue] = related
    return peers


def portfolio_single_pr_markdown(selected, integrated, *, pr_url, project=None):
    """One saved-source PR owner handoff, never a live approval or merge ticket.

    Validate the ENTIRE supplied source before restricting the private output.
    A PR absent from the bounded snapshot is unknown, not merged or free.
    """
    if type(pr_url) is not str or PR.fullmatch(pr_url) is None:
        raise ValueError("Expected canonical existing GitHub PR URL")
    oldest, latest, missing = _admit(selected, integrated)
    projects = project_worklist(selected, integrated)
    candidates = [
        (group, item)
        for group in projects for item in group["candidates"]
        if item["url"] == pr_url
    ]
    if len(candidates) != 1:
        raise ValueError("PR not present exactly once in saved review source")
    group, candidate = candidates[0]
    if project is not None and (
            type(project) is not str or project != group["repository"]):
        raise ValueError("Project filter does not match existing PR evidence")
    head = candidate["head_observed"]
    source_time = integrated["latest_observed_at_utc"]
    gate = candidate["gate_observations"]
    evidence = (
        "Afgeronde checks gezien: " + str(gate["checks_completed"]) +
        "; nog lopend gezien: " + str(gate["checks_pending"]) +
        "; niet succesvol gezien: " + str(gate["checks_not_successful"]) +
        "; onafhankelijke approvals gezien: " +
        str(gate["independent_approvals_observed"])
        if gate is not None else "Geen bruikbare exacte HEAD-gatebron")
    related_issues = candidate["linked_issue_urls"]
    peer_issues = single_pr_related_peers(
        selected, integrated, pr_url=pr_url)
    observed_peer_count = len({peer["url"] for related in
                               peer_issues.values() for peer in related})
    lines = [
        "# zSentinel · privédossier voor één bestaande product-PR",
        "",
        "> Opgeslagen, begrensde bron, NIET LIVE. Dit dossier bevestigt GEEN "
        "eigenaarschap, goedgekeurde verplichte CI/review, merge, release of herstel.",
        "",
        "## Bestaande PR",
        "",
        "- PR: [" + pr_url + "](" + pr_url + ")",
        "- Repository: " + group["repository"],
        "- Bronprioriteit: " + candidate["priority"],
        "- Bronbatch: " + str(candidate["batch_index"]),
        "- Gatebron: " + candidate["gate_source"],
        "- Hoofdblokkade: " + LABELS[candidate["primary_gate"]][0],
        "- Gelijktijdige blokkades: " +
        ", ".join(LABELS[key][0] for key in candidate["concurrent_gates"]),
        "- Waargenomen bron-HEAD: " + (head or "ONBEKEND"),
        "- Onveranderlijke broncommit: " +
        ("https://github.com/" + group["repository"] + "/commit/" + head
         if head else "ONBEKEND"),
        "- Bronintegriteit: " + source_gate_detail(candidate),
        "- Gate-waarneming: " + evidence,
        "",
        "## Herkomst en steekproefgrenzen",
        "",
        "- Oorspronkelijk reviewmanifest SHA256: " +
        integrated["original_review_manifest_sha256"],
        "- Oorspronkelijk board SHA256: " +
        integrated["original_board_sha256"],
        "- Broninterval UTC: " + oldest.isoformat() + " — " + latest.isoformat(),
        "- Brontijd tweede moment UTC: " + source_time,
        "- Leeftijd bij export: " + source_age_warning(latest),
        "- Batches waargenomen: " + str(integrated["observed_batch_count"]) +
        "/" + str(integrated["expected_batch_count"]),
        "- Ontbrekende batches: " +
        (", ".join(map(str, sorted(missing))) or "geen waargenomen"),
        "- Totale oorspronkelijke PR-steekproef: " + str(len(selected)),
        "- Alleen deze opgeslagen PR is uitgelicht; de rest van het portfolio "
        "is niet afgerond, niet vrijgegeven en niet live gecontroleerd.",
        "",
        "## Gekoppelde bronissues (geen fixbewijs)",
        "",
        "Afzonderlijke andere PR-kandidaten bij dezelfde issuevermelding "
        "in deze oorspronkelijke beperkte PR-steekproef: " +
        str(observed_peer_count) + ". Dit is geen bestandsoverlap- "
        "of actueel eigenaarsbewijs.",
        "",
    ]
    for url in related_issues:
        lines.append("- [Issue #" + url.rsplit("/", 1)[-1] + "](" + url + ")")
        others = peer_issues[url]
        if others:
            for peer in others:
                lines.append(
                    "  - Andere PR bij hetzelfde bronissue: [#" +
                    peer["url"].rsplit("/", 1)[-1] + "](" +
                    peer["url"] + ") — " + peer["priority"] +
                    "; gatebron " + peer["gate_source"] +
                    "; HEAD " + (peer["head_observed"] or "ONBEKEND") +
                    "; apart beoordelen, geen bewezen conflict/fix.")
        else:
            lines.append("  - Geen tweede PR in deze opgeslagen PR-steekproef; "
                         "buiten steekproef ONBEKEND.")
    if not related_issues:
        lines.append("- Geen issuevermelding in deze opgeslagen steekproef; "
                     "actuele open issues ONBEKEND.")
    lines.extend([
        "",
        "## Alleen menselijke vervolgstap",
        "",
        candidate["next_action"],
        "",
        "Herlees de huidige GitHub-PR-HEAD, alle verplichte checks, "
        "onafhankelijke review, protected-main, actuele Notion-claims en "
        "releasebevoegdheid. Tot die tijd: merge=NIET_GEAUTORISEERD, "
        "deploy=NIET_GEAUTORISEERD, live=ONBEKEND.",
        "",
    ])
    rendered = "\n".join(lines)
    if len(rendered.encode("utf-8")) > 1024 * 1024:
        raise ValueError("Single PR private source dossier exceeds one MiB")
    return rendered



def portfolio_single_pr_html(selected, integrated, *, pr_url, project=None):
    """Readable one-PR mobile owner card from EXACT saved, no-network evidence."""
    # Reuse the same whole-source admission and PR-only filter as Markdown.
    portfolio_single_pr_markdown(
        selected, integrated, pr_url=pr_url, project=project)
    groups = project_worklist(selected, integrated)
    group, candidate = next(
        (group, item) for group in groups
        for item in group["candidates"] if item["url"] == pr_url)
    e = lambda value: escape(str(value), quote=True)
    head = candidate["head_observed"]
    observed_head = (
        '<a href="https://github.com/' + e(group["repository"]) +
        '/commit/' + head + '" target="_blank" rel="noopener noreferrer">' +
        '<code>' + head + '</code></a>'
        if head else '<strong>BRON HEAD ONBEKEND</strong>')
    peers = single_pr_related_peers(selected, integrated, pr_url=pr_url)
    issues = "".join(
        '<li><a href="' + e(url) +
        '" target="_blank" rel="noopener noreferrer">' +
        e(url) + '</a> — bronvermelding; geen fixbewijs' +
        ('<ul>' + ''.join(
            '<li>Ook in deze bron genoemd bij '
            '<a href="' + e(other["url"]) +
            '" target="_blank" rel="noopener noreferrer">PR #' +
            e(other["url"].rsplit("/", 1)[-1]) + '</a> · HEAD ' +
            e(other["head_observed"] or "ONBEKEND") +
            ' · apart beoordelen (geen bevestigd conflict/fix)</li>'
            for other in peers[url]) + '</ul>'
         if peers[url] else '<p>Geen andere PR in deze beperkte steekproef; '
              'buiten de bron ONBEKEND.</p>') +
        '</li>'
        for url in candidate["linked_issue_urls"]
    ) or "<li>Geen issue gevonden in deze steekproef; actuele issues onbekend.</li>"
    counts = candidate["gate_observations"]
    if counts is None:
        evidence = "Geen verifieerbare opgeslagen HEAD-gateobservatie"
    else:
        evidence = (
            "Checks waargenomen: " + str(counts["checks_completed"]) +
            " afgerond, " + str(counts["checks_pending"]) +
            " lopend, " + str(counts["checks_not_successful"]) +
            " niet succesvol. Onafhankelijke approvals waargenomen: " +
            str(counts["independent_approvals_observed"]))
    head_age = source_age_warning(_admit(selected, integrated)[1])
    missing = integrated["missing_batch_indexes"]
    body = (
        '<!doctype html><html lang="nl"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="referrer" content="no-referrer">'
        '<meta http-equiv="Content-Security-Policy" content="default-src '
        "\'none\'; style-src \'unsafe-inline\'; script-src \'none\'; "
        "connect-src \'none\'; img-src \'none\'; object-src \'none\'; "
        "form-action \'none\'; base-uri \'none\'\">"
        '<title>zSentinel — bestaand PR-brondossier</title>'
        '<style>:root{color-scheme:dark;font-family:system-ui}'
        'body{margin:0;background:#111e30;color:#f2f8ff;line-height:1.55}'
        'main{max-width:880px;margin:auto;padding:clamp(16px,4vw,38px)}'
        'article,aside{border:1px solid #72889e;background:#1b3149;'
        'padding:18px;border-radius:12px;margin:16px 0}'
        'aside{border-color:#eab45a}a{color:#9ddaff;overflow-wrap:anywhere}'
        'a:focus-visible{outline:3px solid #eab45a}'
        'code{overflow-wrap:anywhere}li{margin:10px 0}'
        'h1{font-size:clamp(1.4rem,4vw,2.2rem)}'
        '@media print{body{background:white;color:black}article,aside{background:white;'
        'color:black;border-color:#333}a{color:#034a74}}</style></head><body><main>'
        '<h1>zSentinel · één bestaande product-PR</h1>'
        '<aside><strong>PRIVÉ OPGESLAGEN BRON · NIET LIVE.</strong> '
        'Deze pagina bewijst geen actuele HEAD, eigenaarschap, verplichte CI, '
        'review, main-merge, live status of vrijgave. Andere PRs/claims zijn '
        'onbekend en niet opgelost.</aside>'
        '<article><h2><a href="' + e(pr_url) +
        '" target="_blank" rel="noopener noreferrer">PR #' +
        str(candidate["pr_number"]) + '</a></h2>'
        '<p><strong>' + e(candidate["priority"]) + '</strong> · ' +
        e(group["repository"]) + ' · bronbatch ' +
        str(candidate["batch_index"]) + '</p>'
        '<p><strong>Waargenomen broncommit:</strong> ' + observed_head + '</p>'
        '<p><strong>Gatebron:</strong> ' + e(candidate["gate_source"]) + '</p>'
        '<p><strong>Hoofdblokkade:</strong> ' +
        e(LABELS[candidate["primary_gate"]][0]) + '</p>'
        '<p><strong>Gelijktijdige blokkades:</strong> ' +
        e(", ".join(LABELS[key][0] for key in
                    candidate["concurrent_gates"])) + '</p>'
        '<p><strong>Bronintegriteit:</strong> ' +
        e(source_gate_detail(candidate)) + '</p>'
        '<p>' + e(evidence) + '</p></article>'
        '<article><h2>Gekoppelde bronissues</h2><p>Andere PR-vermeldingen '
        'bij hetzelfde issue worden hieronder getoond. Dit bewijst geen '
        'gedeelde bestandswijziging, eigenaren, oplossing of actuele status.</p><ul>' +
        issues + '</ul></article>'
        '<article><h2>Herkomst</h2><p><strong>' + e(head_age) + '</strong></p>'
        '<p>Observatie UTC: ' + e(integrated["earliest_observed_at_utc"]) +
        ' — ' + e(integrated["latest_observed_at_utc"]) + '</p>'
        '<p>Waargenomen batches: ' +
        str(integrated["observed_batch_count"]) + '/' +
        str(integrated["expected_batch_count"]) +
        ' · Ontbrekende batches: ' +
        e(", ".join(map(str, sorted(missing))) or "geen") + '</p>'
        '<p>Reviewmanifest SHA256: <code>' +
        e(integrated["original_review_manifest_sha256"]) + '</code></p>'
        '<p>Board SHA256: <code>' +
        e(integrated["original_board_sha256"]) + '</code></p>'
        '<p>Oorspronkelijke PR-steekproef: ' + str(len(selected)) +
        ' kandidaat-PRs, niet volledig portfolio.</p></article>'
        '<article><h2>Vervolg voor bevoegde eigenaar</h2><p>' +
        e(candidate["next_action"]) + '</p><p>Controleer GitHub/Notion '
        'op huidige HEAD, actieve claims, volledige verplichte CI, onafhankelijke '
        'review, protected-main en releasebevoegdheid. '
        '<strong>Geen actie automatisch geautoriseerd.</strong></p></article>'
        '<footer>Volledig offline en scriptvrij; alleen vrijwillig openen van '
        'de originele GitHub-links navigeert extern.</footer>'
        '</main></body></html>'
    )
    if len(body.encode("utf-8")) > 1024 * 1024:
        raise ValueError("Single PR offline HTML dossier exceeds one MiB")
    return body


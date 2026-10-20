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


def project_worklist(selected, integrated):
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
    return sorted(indexed.values(), key=lambda p: (
        PRIORITIES.index(p["first_candidate"]["priority"]),
        -p["p0_prs_in_sample"], -p["p1_prs_in_sample"], p["repository"]))


def portfolio_html(selected, integrated):
    """Offline accessible project-level prioritization with original PR links."""
    projects = project_worklist(selected, integrated)
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
            queue.append(
                '<li><a href="' + e(candidate["url"]) +
                '" target="_blank" rel="noopener noreferrer">' +
                e(candidate["priority"]) + ' · #' +
                str(candidate["pr_number"]) + '</a> — ' +
                e(candidate["next_action"]) +
                '<br><small>' + e(observation) + ' · Batch ' +
                str(candidate["batch_index"]) + ' · ' + e(combined) +
                ' · Gekoppelde bronissues: ' + issue_links +
                '</small></li>')
        counts = " · ".join(
            str(p["primary_categories"][key]) + " " + LABELS[key][0]
            for key in LABELS if p["primary_categories"][key])
        other = " · ".join(
            str(p["concurrent_categories"][key]) + " " + LABELS[key][0]
            for key in LABELS if p["concurrent_categories"][key])
        cards.append(
            '<article class="portfolio-project"><h3>' + e(p["repository"]) +
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
    return ('<section class="portfolio-overview" aria-label="zSentinel '
            'portfolio afhandeloverzicht"><h2>Portfolio · bestaande PR '
            'afhandeling</h2><p>Alle aantallen zijn <strong>uitsluitend '
            'uit het opgeslagen bronvenster</strong>. Projecten zonder '
            'waargenomen PR kunnen buiten de steekproef liggen. Geen '
            'eigenaar, merge of live herstel is geverifieerd.</p>'
            '<div class="portfolio-grid">' + ''.join(cards) + '</div></section>')


def portfolio_csv(selected, integrated):
    """One compact Excel-friendly human triage row per observed project."""
    projects = project_worklist(selected, integrated)
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



def portfolio_markdown(selected, integrated):
    """Compact private project-by-project PR queue, without granting ownership.

    Unlike the full saved-gate review, this handoff keeps every observed PR
    next to its own project and original linked issues. A missing source is an
    explicit follow-up, never a successful or free PR.
    """
    projects = project_worklist(selected, integrated)
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
        "- Projecten met bronkandidaten: " + str(len(projects)) +
        "; totaal: " + str(len(selected)),
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


def portfolio_json(selected, integrated):
    """Offline source-bound owner suggestions, never a worker-dispatch contract.

    Clients MUST verify fresh actual owner leases, protected-main policy and
    exact current PR HEADs independently before any authorized action. No
    statement in this report can turn unknown coverage into availability.
    """
    projects = project_worklist(selected, integrated)
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

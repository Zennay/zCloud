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
            "head_observed": (
                gate["head_sha_observed"]
                if gate is not None and category != "source" else None),
            "review_and_merge_authorized": False,
        }
        current = item["first_candidate"]
        order = lambda c: (
            PRIORITIES.index(c["priority"]), list(LABELS).index(c["primary_gate"]),
            c["pr_number"])
        if current is None or order(candidate) < order(current):
            item["first_candidate"] = candidate
    if sum(p["source_prs_observed"] for p in indexed.values()) != len(selected):
        raise ValueError("Incomplete portfolio PR aggregation")
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
            '</a> — ' + e(first["next_action"]) + '</p></article>')
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
            "ONBEKEND", "ONBEKEND", "ONBEKEND", "ONBEKEND",
        ])
    result = "\ufeff" + stream.getvalue()
    if len(result.encode("utf-8")) > 1024 * 1024:
        raise ValueError("Portfolio handoff over one MiB")
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

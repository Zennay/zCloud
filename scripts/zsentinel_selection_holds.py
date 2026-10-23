#!/usr/bin/env python3
"""Private operator rendering for tasks omitted from zSentinel's safe shortlist.

A non-selected candidate is neither an active claim nor a recovery action.
Both text and HTML use this same fail-closed, bounded source contract.
"""
from __future__ import annotations

import re
from html import escape

SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
PRIORITY = {"P0", "P1", "P2", "P3"}
REASONS = {
    "overlapping_suggested_scope": (
        "Bestandsscope overlapt een al voorgestelde taak.",
        "Laat de eigenaar eerst de andere suggestie afhandelen en controleer actuele claims opnieuw.",
    ),
    "shortlist_limit_reached": (
        "De vijf beschikbare shortlistplekken zijn bezet.",
        "Behoud deze taak voor een volgende operatorronde; controleer leases en prioriteit opnieuw.",
    ),
}
COVERAGE_REASONS = {
    "claim_inventory_not_complete", "task_inventory_not_complete",
    "claim_observation_missing", "task_observation_missing",
    "claim_observation_stale_or_future", "task_observation_stale_or_future",
    "active_claim_scope_unknown",
}


def admit(source):
    """Do not turn partial, unbounded or forged suggestion data into owner cards."""
    if source is None:
        return {
            "coverage": "incomplete_or_stale", "coverage_reasons": [],
            "count": 0, "examples": [], "samples_truncated": False,
            "claim_authorized": False, "dispatch_authorized": False,
        }
    if type(source) is not dict:
        raise ValueError("invalid shortlist hold source")
    coverage = source.get("coverage")
    reasons = source.get("coverage_reasons")
    examples = source.get("examples")
    count = source.get("count")
    if (coverage not in ("complete", "incomplete_or_stale")
            or type(reasons) is not list or len(reasons) > len(COVERAGE_REASONS)
            or len(reasons) != len(set(reasons))
            or any(reason not in COVERAGE_REASONS for reason in reasons)
            or (coverage == "complete" and reasons)
            or type(count) is not int or not 0 <= count <= 250
            or type(examples) is not list or len(examples) > 8
            or len(examples) > count
            or source.get("samples_truncated") is not (count > len(examples))
            or source.get("claim_authorized") is not False
            or source.get("dispatch_authorized") is not False):
        raise ValueError("untrusted shortlist hold source")
    seen = set()
    for row in examples:
        if (type(row) is not dict
                or type(row.get("id")) is not str
                or not SLUG.fullmatch(row["id"])
                or type(row.get("project_id")) is not str
                or not SLUG.fullmatch(row["project_id"])
                or row.get("priority") not in PRIORITY
                or row.get("reason") not in REASONS
                or row.get("claim_authorized") is not False
                or row.get("dispatch_authorized") is not False):
            raise ValueError("untrusted shortlist hold row")
        identity = (row["project_id"], row["id"])
        if identity in seen:
            raise ValueError("duplicate shortlist hold task")
        seen.add(identity)
    return source


def operator_lines(source):
    report = admit(source)
    if report["coverage"] != "complete":
        return [
            "Selection/overlap holds: UNKNOWN — task and claim source incomplete or stale.",
            "No observed shortlist hold is proof of free scope or dispatch authorization.",
        ]
    lines = [
        "Otherwise-safe tasks outside the five-slot project-fair shortlist: " +
        str(report["count"]) + " (no claim/dispatch)"
    ]
    for row in report["examples"]:
        lines.append("- HOLD " + row["priority"] + " " + row["project_id"] + "/" +
                     row["id"] + " · " + row["reason"] +
                     " · owner must recheck current claim and scope.")
    if report["samples_truncated"]:
        lines.append("Additional shortlist holds omitted; total preserved.")
    return lines


def html_panel(source):
    report = admit(source)
    complete = report["coverage"] == "complete"
    heading = ("Niet-geselecteerd, verder geschikt werk (" + str(report["count"]) + ")"
               if complete else "Niet-geselecteerd werk — STATUS ONBEKEND")
    intro = ("De P0-eerst shortlist verdeelt plaatsen per project binnen dezelfde prioriteit. "
             "Andere veilige kandidaten krijgen geen claim; ze blijven zichtbaar voor de eigenaar."
             if complete else
             "De taak-/claimbron is onvolledig of verouderd; nul waargenomen voorbeelden "
             "betekent NIET dat alle bestandsscope vrij is.")
    cards = []
    for row in report["examples"]:
        problem, action = REASONS[row["reason"]]
        cards.append(
            '<article class="panel warn"><span class="tag">SHORTLIST-HOLD · ' +
            escape(row["priority"]) + '</span><h3>' +
            escape(row["project_id"]) + " / " + escape(row["id"]) +
            '</h3><p>Probleem: ' + escape(problem) +
            '</p><p>Actie: ' + escape(action) +
            '</p><p>Status: HOLD · geen claim, dispatch, merge of deploy.</p></article>'
        )
    body = "".join(cards) if cards else (
        '<p class="empty">Geen verdere shortlist-kandidaten in deze bron.</p>' if complete else
        '<p class="empty">Bron onbekend; geen vrijgavebewijs.</p>'
    )
    suffix = ('<p class="muted">Overige HOLD-voorbeelden zijn afgekapt; '
              'de telling blijft behouden.</p>' if report["samples_truncated"] else '')
    return ('<section><h2>' + escape(heading) + '</h2><p class="muted">' +
            escape(intro) + '</p>' + body + suffix + '</section>')

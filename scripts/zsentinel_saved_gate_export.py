#!/usr/bin/env python3
"""Private, spreadsheet-ready human PR review queue from saved zSentinel gates.

No network, runner, claims, approvals or merge authority. Input is the same
already admitted manifest/receipts as the standalone HTML operator desk.
"""
from __future__ import annotations

import csv
from io import StringIO
from zsentinel_saved_gate_html import (_admit, _observed_gate, _classify, concurrent_gate_reasons, LABELS, PRIORITIES)

COLUMNS = (
    "Bronmoment oudste UTC", "Bronmoment nieuwste UTC",
    "Reviewmanifest SHA256", "Board SHA256",
    "Prioriteit", "Repository", "PR nummer", "PR URL",
    "Bronissue URLs", "Waargenomen PR HEAD",
    "Blokkadecategorie", "Waargenomen blokkade", "Gelijktijdige blokkades", "Menselijke vervolgactie",
    "Batch status", "Checks afgerond waargenomen",
    "Checks lopend waargenomen", "Checks niet succesvol waargenomen",
    "Onafhankelijke approvals waargenomen",
    "Verplichte CI en review bevestigd", "Claim gecontroleerd",
    "Gemerged op main bevestigd", "Live geverifieerd",
)


def render_csv(selected, integrated):
    """A complete one-row-per-existing-PR private table, never a release receipt.

    All cell values are derived from canonical Zennay IDs, trusted fixed
    labels, exact hashes/UTC source stamps or bounded integer gate counters.
    Avoid importing arbitrary issue titles, notes, metadata, or untrusted
    formula-like text into desktop spreadsheet applications.
    """
    _oldest, _latest, missing = _admit(selected, integrated)
    gates = _observed_gate(integrated, selected, missing)
    if len(selected) > 300:
        raise ValueError("too many PR review candidates")
    stream = StringIO(newline="")
    writer = csv.writer(stream, delimiter=";", quotechar='"',
                        quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerow(COLUMNS)
    seen = set()
    for position, (priority, repository, number, ref) in enumerate(selected):
        url = ref["url"]
        if url in seen:
            raise ValueError("duplicate PR in canonical manifest")
        seen.add(url)
        gate = None if position // 4 + 1 in missing else gates.get(url)
        category = _classify(gate)
        if category not in LABELS:
            raise ValueError("unexpected gate classification")
        label, action = LABELS[category]
        linked = sorted({i["url"] for i in ref["observed_issue_refs"]})
        head = None
        if gate and gate.get("status") == "observed_current_head_partial_gate_evidence":
            head = gate["head_sha_observed"]
        counts = (
            gate["observed_checks_completed"],
            gate["observed_checks_pending"],
            gate["observed_checks_not_successful"],
            gate["observed_independent_exact_head_approvals"],
        ) if head else ("", "", "", "")
        # Unknown is deliberately distinguishable from explicit zero. Nothing
        # here can prove required checks, a current owner, merge or live deploy.
        writer.writerow((
            integrated["earliest_observed_at_utc"],
            integrated["latest_observed_at_utc"],
            integrated["original_review_manifest_sha256"],
            integrated["original_board_sha256"],
            PRIORITIES[priority], repository, number, url,
            " | ".join(linked), head or "ONBEKEND",
            category, label, " | ".join(LABELS[reason][0] for reason in concurrent_gate_reasons(gate)), action,
            ("NIET WAARGENOMEN" if position // 4 + 1 in missing
             else "BRON ONBESCHIKBAAR" if gate is None or not head
             else "GEDEELTELIJK WAARGENOMEN"),
            *counts, "ONBEKEND", "ONBEKEND", "ONBEKEND", "ONBEKEND",
        ))
    # The standard zip-less CSV remains entirely self-contained. UTF-8 BOM
    # helps desktop Excel recognize Dutch text; semicolon is NL locale-friendly.
    result = "\ufeff" + stream.getvalue()
    if len(result.encode("utf-8")) > 1024 * 1024:
        raise ValueError("private CSV exceeds one MiB")
    return result

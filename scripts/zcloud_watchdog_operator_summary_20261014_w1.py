#!/usr/bin/env python3
"""Render a privacy-safe GitHub-hosted, observation-only queue report.

A locally generated report is NOT a release admission, even if classified NOOP.
All invalid, oversized, contradictory or partial input fails closed.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

POLICY = "zcloud-queue-watchdog-admission-v1"
_MAX_BYTES = 32768
_DUTCH_REASON = {
    "incomplete_queue_pages": "GitHub leverde geen complete of consistente wachtrijpagina's.",
    "invalid_job_identity": "Job- of workflow-identiteit ontbreekt, is dubbel of spreekt elkaar tegen.",
    "job_state_changed": "Wachtrij of jobstatus veranderde tijdens de controle.",
    "queue_too_large": "Er staan meer wachtrijgegevens klaar dan veilig gecontroleerd kunnen worden.",
    "main_changed": "De hoofdbranch veranderde tijdens de controle.",
    "observation_too_slow": "Het ophalen duurde te lang voor een betrouwbaar actueel beeld.",
    "github_inventory_untrusted": "De GitHub-gegevens konden niet betrouwbaar worden vastgesteld.",
}





def _unique(pairs):
    output = {}
    for key, value in pairs:
        if key in output:
            raise ValueError("ambiguous_json")
        output[key] = value
    return output


def summarize(raw: bytes, collector_exit: int) -> tuple[str, int]:
    outcome = "DENY"
    explanation = _DUTCH_REASON["github_inventory_untrusted"]
    counts_line = ""
    try:
        if not isinstance(raw, bytes) or not 0 < len(raw) < _MAX_BYTES:
            raise ValueError("oversized_or_empty")
        report = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_unique,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")),
        )
        if not isinstance(report, dict) or report.get("policy") != POLICY:
            raise ValueError("policy_mismatch")
        decision = report.get("decision")
        if decision not in ("NOOP", "REVIEW_ONLY", "DENY") or type(decision) is not str:
            raise ValueError("decision_mismatch")
        if any((
            report.get("mutation_authorized") is not False,
            report.get("production_verified") is not False,
            report.get("inventory_complete") is not False,
            report.get("cancel_run_ids") != [],
        )):
            raise ValueError("privilege_violation")
        expected_exit = 0 if decision == "NOOP" else 1
        if type(collector_exit) is not int or collector_exit != expected_exit:
            raise ValueError("status_mismatch")
        # A displayed count is only a source-local observation, never proof
        # of reliable GitHub provenance or authority for queue operations.
        if decision != "DENY":
            count = report.get("observed_run_count")
            groups = report.get("candidate_duplicate_groups")
            if type(count) is not int or not 0 <= count <= 500 or type(groups) is not list:
                raise ValueError("invalid_observation_counts")
            group_ids = []
            for group in groups:
                if type(group) is not list or not 2 <= len(group) <= 500:
                    raise ValueError("invalid_group")
                if any(type(value) is not int or value <= 0 for value in group):
                    raise ValueError("invalid_group_id")
                group_ids.extend(group)
            if (len(group_ids) != len(set(group_ids))
                or len(group_ids) > count
                or (decision == "NOOP" and groups)
                or (decision == "REVIEW_ONLY" and not groups)):
                raise ValueError("contradictory_observation")
            counts_line = (
                f"- Aangeleverde wachtrijruns: **{count}**; "
                f"groepen ter beoordeling: **{len(groups)}** "
                "(geen bevestiging van productiegezondheid).\n"
            )
        outcome = decision
        if decision == "DENY":
            # Use *only* hardcoded translations, never any external text.
            explanation = _DUTCH_REASON.get(
                report.get("reason") if type(report.get("reason")) is str else None,
                _DUTCH_REASON["github_inventory_untrusted"],
            )
        elif decision == "REVIEW_ONLY":
            explanation = "Mogelijk vergelijkbare wachtrijopdrachten: handmatige beoordeling nodig."
        else:
            explanation = "Geen mogelijke dubbele opdrachten aangetroffen in de aangeleverde observatie."
    except (UnicodeError, ValueError, TypeError, RecursionError, OverflowError):
        outcome = "DENY"
        counts_line = ""
        explanation = _DUTCH_REASON["github_inventory_untrusted"]

    markdown = (
        "### zCloud wachtrij — alleen observatie\n"
        f"- Wachtrijclassificatie: **{outcome}**\n"
        f"- Verklaring: {explanation}\n"
        f"{counts_line}"
        "- Acties annuleren, diensten herstarten of workers forceren: **NIET TOEGESTAAN**\n"
        "- Productiestatus en echte AI-generatie: **NIET BEWEZEN**\n"
        "- GitHub-gegevens zijn geen onafhankelijk bewijs van dezelfde opdrachtintentie.\n"
    )
    return markdown, 0 if outcome == "NOOP" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--collector-exit", required=True, type=int)
    parser.add_argument("--summary-path", required=True, type=Path)
    args = parser.parse_args()

    try:
        with args.report.open("rb") as f:
            raw = f.read(_MAX_BYTES + 1)
    except (OSError, ValueError):
        raw = b""
    markdown, status = summarize(raw, args.collector_exit)
    # This writes a local hosted Actions job summary, never GitHub API state.
    with args.summary_path.open("a", encoding="utf-8") as stream:
        stream.write(markdown)
    return status


if __name__ == "__main__":
    raise SystemExit(main())

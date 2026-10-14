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


def _unique(pairs):
    output = {}
    for key, value in pairs:
        if key in output:
            raise ValueError("ambiguous_json")
        output[key] = value
    return output


def summarize(raw: bytes, collector_exit: int) -> tuple[str, int]:
    outcome = "DENY"
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
        outcome = decision
    except (UnicodeError, ValueError, TypeError, RecursionError, OverflowError):
        outcome = "DENY"

    markdown = (
        "### zCloud wachtrij — alleen observatie\\n"
        f"- Wachtrijclassificatie: **{outcome}**\\n"
        "- Acties annuleren, diensten herstarten of workers forceren: **NIET TOEGESTAAN**\\n"
        "- Productiestatus en echte AI-generatie: **NIET BEWEZEN**\\n"
        "- GitHub-gegevens zijn geen onafhankelijk bewijs van dezelfde opdrachtintentie.\\n"
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

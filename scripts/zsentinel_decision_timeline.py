#!/usr/bin/env python3
"""Offline zSentinel incident timeline from two to eight private saved decisions.

An absent incident in a partial later snapshot is NOT proof of recovery.
No GitHub/Notion/queue/VPS requests, claims, retries or automatic releases.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

from zsentinel_incident_desk import _source
from zsentinel_saved_operator_replay import archived_decision
from zsentinel_private_output import write_private_output

MAX_WINDOWS = 8
MAX_ROWS = 400
MAX_UNIQUE = 1600
SEVERITIES = ("P0", "P1", "P2", "P3")


def collect_history(paths, now: datetime | None = None):
    if not isinstance(paths, list) or not 2 <= len(paths) <= MAX_WINDOWS:
        raise ValueError("select two through eight saved private decisions")
    previous = None
    seen_paths = set()
    windows, by_key = [], {}
    for index, path in enumerate(paths, 1):
        if not isinstance(path, Path):
            raise ValueError("snapshot source must be a file path")
        canonical = path.resolve(strict=True)
        if canonical in seen_paths:
            raise ValueError("same snapshot supplied more than once")
        seen_paths.add(canonical)
        decision, moment = archived_decision(path, now=now)
        if previous is not None and moment <= previous:
            raise ValueError("snapshots must be in strictly increasing UTC source order")
        previous = moment
        source = _source(decision)
        incidents = source.get("incidents")
        if type(incidents) is not list or len(incidents) > 200:
            raise ValueError("invalid source incident inventory")
        digest = hashlib.sha256(json.dumps(
            decision, sort_keys=True, ensure_ascii=True,
            separators=(",", ":"), allow_nan=False).encode("ascii")).hexdigest()
        windows.append({
            "index": index, "observed_at_utc": moment.isoformat(),
            "normalized_source_sha256": digest,
            "observed_incident_count": len(incidents),
            "source_partial": True, "ownership_verified": False,
        })
        for item in incidents:
            key = item["id"]
            row = by_key.setdefault(key, {
                "id": key, "project": item["project"],
                "problem": item["problem"], "events": [],
            })
            if (row["project"], row["problem"]) != (
                    item["project"], item["problem"]):
                raise ValueError("incident identity used across different projects/problems")
            row["events"].append({
                "window": index, "source_at": moment.isoformat(),
                "priority": item["priority"], "owner_label": item["owner"],
                "action": item["action"], "status": item["status"],
                "human": item["human"], "review_by": item["review_by"],
            })
    if len(by_key) > MAX_UNIQUE:
        raise ValueError("too many distinct historical incident identities")
    rows = []
    for item in by_key.values():
        events = item["events"]
        last = events[-1]
        prior = events[-2] if len(events) > 1 else None
        seen_final = last["window"] == len(windows)
        changed = bool(prior and any(
            prior[field] != last[field] for field in (
                "priority", "owner_label", "action", "status", "human", "review_by")))
        rows.append({
            "id": item["id"], "project": item["project"],
            "problem": item["problem"], "observed_windows": len(events),
            "last_window": last["window"],
            "last_priority": last["priority"],
            "last_owner_label_unverified": last["owner_label"],
            "last_action_advisory": last["action"],
            "last_status": last["status"],
            "human_last_observed": last["human"],
            "last_original_review_target": last["review_by"],
            "observed_in_final_sample": seen_final,
            "classification": (
                "observed_in_last_partial_source" if seen_final
                else "not_in_final_sample_reconciliation_required"),
            "observed_field_changes": changed,
            "no_owner_recovery_or_merge_inference": True,
            "events": events,
        })
    rows.sort(key=lambda row: (
        SEVERITIES.index(row["last_priority"]),
        not row["human_last_observed"], not row["observed_in_final_sample"],
        -row["observed_windows"], row["project"], row["id"],
    ))
    return {
        "schema": "zsentinel-private-router-timeline-v1",
        "source_count": len(windows), "sources": windows,
        "first_source_observed_at_utc": windows[0]["observed_at_utc"],
        "last_source_observed_at_utc": windows[-1]["observed_at_utc"],
        "unique_incidents_sampled": len(rows),
        "visible_incidents": min(MAX_ROWS, len(rows)),
        "rows_truncated": len(rows) > MAX_ROWS,
        "observed_in_final_sample": sum(
            row["observed_in_final_sample"] for row in rows),
        "not_observed_in_final_sample_needs_reconciliation": sum(
            not row["observed_in_final_sample"] for row in rows),
        "multi_window_observed": sum(
            row["observed_windows"] >= 2 for row in rows),
        "source_coverage_partial": True,
        "owner_claim_verified": False, "recovery_verified": False,
        "merge_authorized": False, "writes_performed": False,
        "items": rows[:MAX_ROWS],
    }


def render_text(report):
    lines = [
        "zSentinel: HISTORISCHE incidenttijdlijn / GEEN LIVE HERSTELBEWIJS",
        "Bronmomenten %d, eerste %s, laatste %s" % (
            report["source_count"], report["first_source_observed_at_utc"],
            report["last_source_observed_at_utc"]),
        "Uniek gezien %d, getoond %d, afgekapt %s" % (
            report["unique_incidents_sampled"], report["visible_incidents"],
            report["rows_truncated"]),
        "Niet in laatste deelsample %d: NIET bewezen hersteld!" %
        report["not_observed_in_final_sample_needs_reconciliation"],
    ]
    for row in report["items"]:
        lines.extend([
            "%s %s %s · %s" % (
                row["last_priority"], row["project"], row["problem"], row["id"]),
            "Waargenomen in %d/%d vensters · %s" % (
                row["observed_windows"], report["source_count"],
                row["classification"]),
            "Actieadvies: %s" % row["last_action_advisory"],
            "Eigenaarlabel (onbevestigd) %s · status %s · beoordeling %s" % (
                row["last_owner_label_unverified"], row["last_status"],
                row["last_original_review_target"]),
        ])
    return "\n".join(lines) + "\n"


def render_markdown(report):
    lines = [
        "# zSentinel · historische P0/P1 herstelbalie", "",
        "> Geen huidige CI-, eigenaar- of runnerwaarheid. Afwezigheid in een latere ",
        "> gedeeltelijke waarneming bewijst geen opgelost incident.", "",
        "- Bron eerste: " + report["first_source_observed_at_utc"],
        "- Bron laatste: " + report["last_source_observed_at_utc"],
        "- Unieke incidenten: " + str(report["unique_incidents_sampled"]),
        "- Niet opnieuw gezien, opvolgen: " +
        str(report["not_observed_in_final_sample_needs_reconciliation"]),
        "- Afgekapt: " + str(report["rows_truncated"]), "",
    ]
    def inert(value):
        value = value.replace("\\", "\\\\")
        for char in ("<", ">", "#", "[", "]", "*", "_", "|", chr(96), "!"):
            value = value.replace(char, "\\" + char)
        return value.replace("\n", " ")
    for row in report["items"]:
        lines.extend([
            "## " + inert(row["last_priority"] + " · " +
                           row["project"] + " · " + row["problem"]),
            "- Incident: " + row["id"],
            "- Bronvensters: " + str(row["observed_windows"]),
            "- Laatste bronstatus: " + row["classification"],
            "- Eigenaarlabel (niet geverifieerd): " +
            inert(row["last_owner_label_unverified"]),
            "- Advies: " + inert(row["last_action_advisory"]),
            "- Laatste originele beoordelingsdatum: " +
            row["last_original_review_target"], "",
        ])
    return "\n".join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(description="Offline historical zSentinel incident timeline")
    p.add_argument("--snapshot", type=Path, action="append", required=True,
                   help="two to eight 0600 owner-private saved JSON reports, chronologically")
    p.add_argument("--format", choices=("html", "json", "text", "md"), default="html")
    p.add_argument("--output", type=Path, required=True,
                   help="new owner-private 0600 output report")
    args = p.parse_args(argv)
    try:
        extension = {"html": "html", "json": "json", "text": "txt", "md": "md"}[args.format]
        if args.output.suffix != "." + extension:
            raise ValueError("output extension does not match the chosen format")
        if any(args.output.resolve() == path.resolve() for path in args.snapshot):
            raise ValueError("history output cannot overwrite a source")
        report = collect_history(args.snapshot)
        if args.format == "html":
            from zsentinel_decision_timeline_html import render_html
            rendered = render_html(report)
        elif args.format == "md":
            rendered = render_markdown(report)
        elif args.format == "text":
            rendered = render_text(report)
        else:
            rendered = json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2)
        write_private_output(args.output, rendered, extension)
    except (OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
        p.exit(2, "zSentinel historical timeline unavailable: " + str(exc)[:160] + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

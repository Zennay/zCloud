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
from zsentinel_queue_pressure_timeline import observed_queue, summarize_queue_windows
from zsentinel_worker_continuity import observe_saved_workers, summarize_worker_windows

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
            "observed_p0_count": sum(i["priority"] == "P0" for i in incidents),
            "observed_p1_count": sum(i["priority"] == "P1" for i in incidents),
            "observed_human_only_count": sum(i["human"] for i in incidents),
            "source_partial": True, "ownership_verified": False,
            "queue_pressure": observed_queue(decision, moment),
            "worker_progress": observe_saved_workers(decision, moment),
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
        first = events[0]
        seen_final = last["window"] == len(windows)
        highest = min(
            (e["priority"] for e in events),
            key=lambda priority: SEVERITIES.index(priority))
        changed = any(
            any(e[field] != first[field] for field in (
                "priority", "owner_label", "action", "status", "human", "review_by"))
            for e in events[1:])
        gaps = sum(
            current["window"] > before["window"] + 1
            for before, current in zip(events, events[1:]))
        rows.append({
            "id": item["id"], "project": item["project"],
            "problem": item["problem"], "observed_windows": len(events),
            "first_window": first["window"],
            "last_window": last["window"],
            "highest_observed_priority": highest,
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
            "disappeared_and_reappeared_sample_gaps": gaps,
            "no_owner_recovery_or_merge_inference": True,
            "events": events,
        })
    rows.sort(key=lambda row: (
        SEVERITIES.index(row["highest_observed_priority"]),
        not row["human_last_observed"], not row["observed_in_final_sample"],
        -row["observed_windows"], row["project"], row["id"],
    ))
    queue_history = summarize_queue_windows(windows)
    worker_history = summarize_worker_windows(windows)
    return {
        "schema": "zsentinel-private-router-timeline-v1",
        "queue_pressure_history": queue_history,
        "worker_continuity_history": worker_history,
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
        "disappeared_then_reobserved": sum(
            row["disappeared_and_reappeared_sample_gaps"] > 0 for row in rows),
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
        "Queuebronparen vergelijkbaar %d, onbekend %d (claimpogingen != retries)" % (
            report["queue_pressure_history"]["compared_pairs"],
            report["queue_pressure_history"]["unknown_pairs"]),
        "Status=blocked bronparen vergelijkbaar %d, onbekend %d (aantallen != herstelbewijs)" % (
            report["queue_pressure_history"]["blocked_queue_compared_pairs"],
            report["queue_pressure_history"]["blocked_queue_unknown_pairs"]),
        "Workerbronparen deels of geheel ONBEKEND %d; nieuwe generatiesignalen %d "
        "(GEEN productlevering)" % (
            report["worker_continuity_history"]["unknown_or_partial_pairs"],
            report["worker_continuity_history"]["observed_generation_signal_pairs"]),
    ]
    queue_history = report["queue_pressure_history"]
    lines.append("Aanhoudende eigenaarssignalen %d%s (3 complete opeenvolgende bronnen, GEEN retries):" % (
        queue_history["sustained_owner_attention_total"],
        " AFGKAPT" if queue_history["sustained_owner_attention_truncated"] else ""))
    for row in queue_history["sustained_owner_attention"]:
        lines.append("  %s %s vensters %d-%d: %s [%s]; actuele claim opnieuw inspecteren." % (
            row["priority"], row["project_id"], row["first_window"],
            row["last_window"], row["signal"],
            ", ".join(str(x) for x in row["observed_counts"])))
    for pair in report["queue_pressure_history"]["adjacent_comparisons"]:
        if pair["deltas"] is None:
            lines.append("Queue venster %d → %d: ONBEKEND, bronhiaat." %
                         (pair["from_window"], pair["to_window"]))
        else:
            delta = pair["deltas"]
            lines.append(
                "Queue venster %d → %d · wachtrij %+d, hoge claimpogingen %+d, "
                "verlopen lease-rijen %+d (slechts aggregate verschillen, geen retries)." %
                (pair["from_window"], pair["to_window"],
                 delta["queued_items"], delta["high_claim_attempt_items"],
                 delta["expired_owner_lease_rows"]))
            lines.append(
                "  Inconsistente leaseklokrijen: " +
                ("ONBEKEND" if delta["inconsistent_owner_lease_window_rows"] is None
                 else "%+d" % delta["inconsistent_owner_lease_window_rows"]) +
                "; eigenaar verifieert claimed_at en claim_expires.")
        blocked = pair["blocked_queue_comparison"]
        if blocked["delta"] is None:
            lines.append("  Status=blocked: ONBEKEND door bronhiaat of verouderde waarneming.")
        else:
            lines.append("  Status=blocked rijen: %d → %d (Δ %+d); verandering is geen bewijs van herstel." %
                         (blocked["from_total"], blocked["to_total"], blocked["delta"]))
            if blocked["project_deltas"] is not None:
                for group in blocked["project_deltas"]:
                    if group["blocked_rows_delta_not_resolution_proof"] != 0:
                        lines.append("    %s %s · status=blocked %+d · eigenaar moet actuele rij verifiëren." %
                                     (group["priority"], group["project_id"],
                                      group["blocked_rows_delta_not_resolution_proof"]))
    for pair in report["queue_pressure_history"]["adjacent_comparisons"]:
        if pair["observed_increased_refs_total"] is None:
            continue
        lines.append("Wachtrijvensters %d-%d: %d geobserveerde positieve claimpogingstijgingen (geen faalbewijs)" % (
            pair["from_window"], pair["to_window"], pair["observed_increased_refs_total"]))
        for row in pair["observed_increased_queue_references"]:
            lines.append("  %s %s private bronref %s: %d → %d pogingen; eigenaar verifieert actuele lease" % (
                row["priority"], row["project_id"], row["private_queue_ref"],
                row["previous_attempts"], row["current_attempts"]))
    for pair in report["worker_continuity_history"]["adjacent_comparisons"]:
        lines.append("Worker venster %d → %d · %s · vergeleken slots %d" %
                     (pair["from_window"], pair["to_window"],
                      pair["comparison"], pair["workers_compared"]))
        for worker in pair["entries"]:
            lines.append("  %s slot %d · %s · eigenaarcheck %s" %
                         (worker["project_id"], worker["worker_slot"],
                          worker["classification"], worker["later_owner_followup"]))
    for row in report["items"]:
        lines.extend([
            "%s %s %s · %s" % (
                row["last_priority"], row["project"], row["problem"], row["id"]),
            "Waargenomen in %d/%d vensters · %s · hoogste historische urgentie %s" % (
                row["observed_windows"], report["source_count"],
                row["classification"], row["highest_observed_priority"]),
            "Verdween en verscheen opnieuw in steekproeven %d keer: GEEN bewijs van tussentijds herstel" %
            row["disappeared_and_reappeared_sample_gaps"],
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
        "- Afgekapt: " + str(report["rows_truncated"]),
        "- Wachtrijvergelijkingen: " +
        str(report["queue_pressure_history"]["compared_pairs"]) +
        " bronparen; onbekend " +
        str(report["queue_pressure_history"]["unknown_pairs"]),
        "- Status=blocked vergelijkingen: " +
        str(report["queue_pressure_history"]["blocked_queue_compared_pairs"]) +
        " bronparen; onbekend " +
        str(report["queue_pressure_history"]["blocked_queue_unknown_pairs"]), "",
        "## Historische SQLite-wachtrijdruk (geen claim- of retryrechten)", "",
    ] + [
        ("- Vensters %d → %d: %s" % (
            pair["from_window"], pair["to_window"],
            ("BRON ONBEKEND" if pair["deltas"] is None else
             "wachtenden %+d, hoge claimpogingen %+d, verlopen lease-rijen %+d"
             % (pair["deltas"]["queued_items"],
                pair["deltas"]["high_claim_attempt_items"],
                pair["deltas"]["expired_owner_lease_rows"]))))
        for pair in report["queue_pressure_history"]["adjacent_comparisons"]
    ] + [
        "",
        "## Historische rijen met status=blocked (geen herstelbewijs)", "",
    ] + [
        ("- Vensters %d → %d: ONBEKEND door bronhiaat of verouderde bron." %
         (pair["from_window"], pair["to_window"])
         if pair["blocked_queue_comparison"]["delta"] is None else
         "- Vensters %d → %d: status=blocked %d → %d (Δ %+d); "
         "aantalsverandering bewijst geen taakherstel." %
         (pair["from_window"], pair["to_window"],
          pair["blocked_queue_comparison"]["from_total"],
          pair["blocked_queue_comparison"]["to_total"],
          pair["blocked_queue_comparison"]["delta"]))
        for pair in report["queue_pressure_history"]["adjacent_comparisons"]
    ] + [
        ("  - " + group["priority"] + " " + group["project_id"] +
         " · status=blocked %+d · actuele rij door bevoegde eigenaar controleren."
         % group["blocked_rows_delta_not_resolution_proof"])
        for pair in report["queue_pressure_history"]["adjacent_comparisons"]
        for group in (pair["blocked_queue_comparison"].get("project_deltas") or [])
        if group["blocked_rows_delta_not_resolution_proof"] != 0
    ] + [
        "",
        "## Leaseklokfouten per oude SQLite-aggregaat", "",
    ] + [
        "- Vensters " + str(pair["from_window"]) + "–" + str(pair["to_window"]) +
        ": " + ("ONBEKEND" if pair["deltas"] is None or
                pair["deltas"]["inconsistent_owner_lease_window_rows"] is None else
                str(pair["deltas"]["inconsistent_owner_lease_window_rows"])) +
        "; huidige claimed_at/claim_expires en eigenaarlease eerst controleren."
        for pair in report["queue_pressure_history"]["adjacent_comparisons"]
    ] + [
        "", "## Brongebonden toenemende claimpogingen per privé-wachtrijreferentie", "",
    ] + [
        "- Vensters " + str(pair["from_window"]) + "–" +
        str(pair["to_window"]) + ": " +
        ("ONBEKEND door bronhiaat" if pair["observed_increased_refs_total"] is None else
         str(pair["observed_increased_refs_total"]) + " positieve verschillen" +
         (" (AFGEKAPT)" if pair["observed_increased_refs_truncated"] else "")) +
        ". Bronreferenties bewijzen geen mislukte retries of claimrechten."
        for pair in report["queue_pressure_history"]["adjacent_comparisons"]
    ] + [
        "- " + row["priority"] + " " + row["project_id"] +
        " · privéref " + row["private_queue_ref"] +
        ": " + str(row["previous_attempts"]) + " → " +
        str(row["current_attempts"]) + " claimpogingen; eigenaar controleert originele rij."
        for pair in report["queue_pressure_history"]["adjacent_comparisons"]
        for row in pair["observed_increased_queue_references"]
    ] + [
        "", "## Aanhoudende eigenaarssignalen (geen identiek taakbewijs)", "",
        "Volledige bronreeksen van drie: " +
        str(report["queue_pressure_history"]["eligible_complete_three_window_sequences"]) +
        "; groepen " + str(report["queue_pressure_history"]["sustained_owner_attention_total"]) +
        (" (AFGEKAPT)" if report["queue_pressure_history"]["sustained_owner_attention_truncated"]
         else "") + ".",
    ] + [
        "- " + row["priority"] + " " + row["project_id"] + " · " +
        row["signal"] + " · vensters " +
        str(row["first_window"]) + "–" + str(row["last_window"]) +
        "; geobserveerde aantallen " + "/".join(str(x) for x in row["observed_counts"]) +
        "; eigenaar controleert opnieuw, GEEN leasevrijgave of retry."
        for row in report["queue_pressure_history"]["sustained_owner_attention"]
    ] + [
        "", "## Historische workergebeurtenissen (geen product-/claimbewijs)", "",
    ] + [
        ("- Vensters %d → %d · %s · slots %d" %
         (pair["from_window"], pair["to_window"],
          pair["comparison"], pair["workers_compared"]))
        for pair in report["worker_continuity_history"]["adjacent_comparisons"]
    ] + [
        ("  - %s slot %d: %s; eigenaarcontrole: %s" %
         (worker["project_id"], worker["worker_slot"],
          worker["classification"], worker["later_owner_followup"]))
        for pair in report["worker_continuity_history"]["adjacent_comparisons"]
        for worker in pair["entries"]
    ] + [
        "",
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

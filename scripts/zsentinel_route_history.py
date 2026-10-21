#!/usr/bin/env python3
"""Private P0-first route history across existing portfolio snapshots.

Uses already-read owner-private reports. Never reads providers or writes a
queue, worker lease, incident source, merge state or deployment.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import datetime, timedelta, timezone
from html import escape

from zsentinel_route_continuity import _decision, _index, _queue_origin, PRIORITY

SCHEMA = "zsentinel-routing-history-v1"
MAX_WINDOWS = 8
MAX_VISIBLE = 48
IDENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
ACTIONS = {
    "route_changed": "Controleer de huidige eigenaar, lease, PR en vereiste uitvoeringsgates.",
    "appeared_in_later_sample": "Controleer oorspronkelijke taak en claims; nieuw in de steekproef is niet nieuw werk.",
    "not_reobserved": "Controleer de oorspronkelijke bron; afwezig is NIET opgelost of vrijgegeven.",
}
KINDS = {"route_changed": 0, "not_reobserved": 1, "appeared_in_later_sample": 2}


def _unknown(reason, count, stamps=None):
    return {
        "schema": SCHEMA, "coverage": reason,
        "window_count": count, "window_utc": stamps or [],
        "source_window_coverage": [], "same_queue_origin_verified": False,
        "source_live_verified": False, "total_events_observed": None,
        "visible_events": [], "events_truncated": False,
        "task_recovered_verified": False, "owner_claim_verified": False,
        "claim_authorized": False, "dispatch_authorized": False,
        "merge_authorized": False, "deploy_authorized": False,
        "actions_performed": False,
    }


def build_history(snapshots, *, now=None, allow_historical=False):
    """Aggregate adjacent source transitions, preserving missing-sample uncertainty.

    Only consecutive windows can establish an observed route change. A task
    seen after a gap is a new observation in that slice, NOT a change proof.
    The queue identity must match every source, including the earliest one.
    """
    if (type(snapshots) is not list or not 2 <= len(snapshots) <= MAX_WINDOWS
            or type(allow_historical) is not bool):
        raise ValueError("expected two to eight saved routing windows")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("timezone-aware history clock required")
    now = now.astimezone(timezone.utc)
    windows = []
    for snapshot in snapshots:
        decision = _decision(snapshot)
        if decision is None:
            return _unknown("older_routing_source_missing", len(snapshots))
        index = _index(decision)
        windows.append((index, _queue_origin(snapshot, decision)))
    clocks = [entry["observed"] for entry, _ in windows]
    stamps = [clock.isoformat(timespec="seconds").replace("+00:00", "Z")
              for clock in clocks]
    if clocks[-1] > now + timedelta(minutes=2):
        raise ValueError("future current routing history source")
    if not allow_historical and now - clocks[-1] > timedelta(minutes=10):
        return _unknown("current_source_stale", len(windows), stamps)
    for earlier, later in zip(clocks, clocks[1:]):
        if not earlier < later or later - earlier > timedelta(hours=72):
            return _unknown("chronology_out_of_range", len(windows), stamps)
    origins = [origin for _, origin in windows]
    if any(origin is None for origin in origins) or len(set(origins)) != 1:
        return _unknown("queue_origin_missing_or_changed", len(windows), stamps)
    events = []
    coverages = []
    for index, (entry, _) in enumerate(windows):
        coverages.append({
            "window": index + 1, "observed_at_utc": stamps[index],
            "routing_inventory_complete": entry["complete"],
            "examples_truncated": entry["sample_truncated"],
            "task_id_scope": "observed_bounded_sample_only",
        })
    for offset in range(1, len(windows)):
        prior = windows[offset - 1][0]["rows"]
        current = windows[offset][0]["rows"]
        for key, row in current.items():
            earlier = prior.get(key)
            if earlier is None:
                kind = "appeared_in_later_sample"
            elif any(earlier[field] != row[field] for field in (
                    "priority", "reason", "state")):
                kind = "route_changed"
            else:
                continue
            events.append({
                "window_before": offset, "window_after": offset + 1,
                "source_after_utc": stamps[offset], "project_id": key[0],
                "task_id": key[1], "priority": row["priority"],
                "kind": kind, "before_reason": earlier["reason"] if earlier else None,
                "after_reason": row["reason"],
                "next_owner_action": ACTIONS[kind],
                "claim_authorized": False, "dispatch_authorized": False,
            })
        for key, row in prior.items():
            if key in current:
                continue
            kind = "not_reobserved"
            events.append({
                "window_before": offset, "window_after": offset + 1,
                "source_after_utc": stamps[offset], "project_id": key[0],
                "task_id": key[1], "priority": row["priority"],
                "kind": kind, "before_reason": row["reason"],
                "after_reason": None,
                "next_owner_action": ACTIONS[kind],
                "claim_authorized": False, "dispatch_authorized": False,
            })
    # Strict priority first, then newest verified observation, then
    # changed route/unknown disappearance/reappearance and stable identity.
    events.sort(key=lambda e: (
        PRIORITY[e["priority"]], -e["window_after"],
        KINDS[e["kind"]], e["project_id"], e["task_id"]))
    return {
        "schema": SCHEMA,
        "coverage": ("historical_sampled_series" if allow_historical else
                     "current_sampled_series"),
        "window_count": len(windows), "window_utc": stamps,
        "source_window_coverage": coverages,
        "same_queue_origin_verified": True,
        "source_live_verified": False,
        "total_events_observed": len(events),
        "visible_events": events[:MAX_VISIBLE],
        "events_truncated": len(events) > MAX_VISIBLE,
        "task_recovered_verified": False, "owner_claim_verified": False,
        "claim_authorized": False, "dispatch_authorized": False,
        "merge_authorized": False, "deploy_authorized": False,
        "actions_performed": False,
    }


def _admit(report):
    if (type(report) is not dict or report.get("schema") != SCHEMA
            or report.get("coverage") not in (
                "historical_sampled_series", "current_sampled_series",
                "older_routing_source_missing", "current_source_stale",
                "chronology_out_of_range", "queue_origin_missing_or_changed")
            or any(report.get(field) is not False for field in (
                "source_live_verified", "task_recovered_verified",
                "owner_claim_verified", "claim_authorized",
                "dispatch_authorized", "merge_authorized",
                "deploy_authorized", "actions_performed"))
            or type(report.get("window_count")) is not int
            or not 2 <= report["window_count"] <= MAX_WINDOWS
            or type(report.get("visible_events")) is not list
            or len(report["visible_events"]) > MAX_VISIBLE):
        raise ValueError("untrusted routing history owner report")
    for event in report["visible_events"]:
        if (type(event) is not dict
                or event.get("kind") not in KINDS
                or event.get("priority") not in PRIORITY
                or type(event.get("project_id")) is not str
                or not IDENT.fullmatch(event["project_id"])
                or type(event.get("task_id")) is not str
                or not IDENT.fullmatch(event["task_id"])
                or event.get("next_owner_action") != ACTIONS[event["kind"]]
                or event.get("claim_authorized") is not False
                or event.get("dispatch_authorized") is not False
                or type(event.get("window_before")) is not int
                or type(event.get("window_after")) is not int
                or not 1 <= event["window_before"] < event["window_after"] <= report["window_count"]):
            raise ValueError("invalid or authorizing history owner event")
    return report


def owner_csv(report):
    """Portable, historical-only owner triage across all inspected windows."""
    source = _admit(report)
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow([
        "source_status", "observed_after_utc", "window_before", "window_after",
        "priority", "project_id", "task_id", "observation",
        "previous_route", "next_route", "owner_next_step",
        "owner_claim_verified", "claim_authorized", "dispatch_authorized",
        "merge_authorized", "task_recovered_verified",
    ])
    if source["coverage"] not in ("historical_sampled_series",
                                  "current_sampled_series"):
        writer.writerow([source["coverage"], "", "", "", "", "", "", "UNKNOWN",
                         "", "", "Original sources must be rechecked; no action authorized.",
                         "false", "false", "false", "false", "false"])
        return output.getvalue()
    for event in source["visible_events"]:
        writer.writerow([
            source["coverage"], event["source_after_utc"],
            event["window_before"], event["window_after"],
            event["priority"], event["project_id"], event["task_id"],
            event["kind"], event["before_reason"] or "",
            event["after_reason"] or "", event["next_owner_action"],
            "false", "false", "false", "false", "false",
        ])
    if source["events_truncated"]:
        writer.writerow([source["coverage"], "", "", "", "", "", "",
                         "SAMPLE_TRUNCATED_UNKNOWN", "", "",
                         "More events omitted; no source or queue all-clear.",
                         "false", "false", "false", "false", "false"])
    return output.getvalue()


def owner_text(report):
    source = _admit(report)
    lines = ["zSentinel · meer-venster herstelgeschiedenis · ALLEEN ADVIES"]
    if source["coverage"] not in ("historical_sampled_series",
                                  "current_sampled_series"):
        return "\n".join(lines + [
            "Tijdlijn ONBEKEND (" + source["coverage"] +
            "); geen vrijgave, herstelbewijs of claimautorisatie."])
    lines.append("Bronvensters: " + str(source["window_count"]) +
                 " · bronbewegingen zichtbaar: " +
                 str(len(source["visible_events"])) + " / " +
                 str(source["total_events_observed"]) +
                 " · bronvoorbeelden blijven beperkt.")
    if source["coverage"] == "historical_sampled_series":
        lines.append("HISTORISCHE REPLAY: geen actuele claim/runner/gate bewezen.")
    if any(not row["routing_inventory_complete"]
           for row in source["source_window_coverage"]):
        lines.append("Een of meer bronvensters zijn incompleet of afgekapt.")
    for e in source["visible_events"]:
        prior = e["before_reason"] or "niet eerder in steekproef"
        after = e["after_reason"] or "NIET HERWAARGENOMEN, NIET OPGELOST"
        lines.append("- " + e["priority"] + " " + e["project_id"] + "/" +
                     e["task_id"] + " · " + str(e["window_before"]) + "→" +
                     str(e["window_after"]) + " · " + prior + " → " + after +
                     " · " + e["next_owner_action"])
    if source["events_truncated"]:
        lines.append("Extra bronovergangen verborgen; totaal behouden, geen volledig werkoverzicht.")
    return "\n".join(lines)


def owner_html(report):
    source = _admit(report)
    if source["coverage"] not in ("historical_sampled_series",
                                  "current_sampled_series"):
        return ('<section class="panel warn"><h2>Herstelgeschiedenis · ONBEKEND</h2>'
                '<p>' + escape(owner_text(source).splitlines()[-1]) +
                '</p></section>')
    cards = []
    for e in source["visible_events"]:
        before = e["before_reason"] or "niet in vorig bronvenster"
        after = e["after_reason"] or "niet opnieuw gezien; NIET opgelost"
        cards.append(
            '<article class="panel read"><span class="tag">' +
            escape(e["kind"]) + '</span><h3>' + escape(e["priority"]) +
            " · " + escape(e["project_id"]) + "/" + escape(e["task_id"]) +
            '</h3><p>Bron ' + str(e["window_before"]) + " → " +
            str(e["window_after"]) + " · " + escape(e["source_after_utc"]) +
            '</p><p>Probleem: ' + escape(before) + " → " + escape(after) +
            '</p><p>Actie: ' + escape(e["next_owner_action"]) +
            '</p><p class="muted">HOLD: geen claim, retry, merge of deploy.</p></article>'
        )
    notes = ("HISTORISCHE REPLAY. " if
             source["coverage"] == "historical_sampled_series" else "")
    if any(not r["routing_inventory_complete"] for r in
           source["source_window_coverage"]):
        notes += "Bronvensters incompleet; geen bewijs van vrij werk. "
    return ('<section aria-label="Historische herstelroutering"><h2>'
            'Routegeschiedenis · P0-eerst</h2><p class="muted">' +
            escape(notes + "Geen live claim- of hersteltoestemming. ") +
            str(source["window_count"]) + ' bronvensters · ' +
            str(source["total_events_observed"]) + ' waargenomen overgangen.</p>' +
            (''.join(cards) if cards else
             '<p>Geen verschillen in de beschikbare steekproef; '
             'niet bewezen dat alle werk is afgerond.</p>') +
            ('<p>Overige overgangen afgekapt.</p>'
             if source["events_truncated"] else "") + '</section>')

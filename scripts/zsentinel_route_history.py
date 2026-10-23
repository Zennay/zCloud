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
        "repeated_hold_candidates": [], "repeated_hold_total": None,
        "repeated_hold_candidates_truncated": False,
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
    # Preserve completed repeated-HOLD episodes even when the last bounded
    # sample no longer contains the task. Disappearance is not resolution.
    # Split episodes at a gap, a suggested-only state or a reason change.
    consecutive = {}
    completed = []

    def finish_episode(row):
        if row["count"] < 3:
            return
        completed.append({
            "project_id": row["project_id"], "task_id": row["task_id"],
            "priority": row["priority"], "hold_reason": row["hold_reason"],
            "consecutive_source_observations": row["count"],
            "first_window": row["first_window"], "last_window": row["last_window"],
            "first_observed_utc": row["first_observed_utc"],
            "last_observed_utc": row["last_observed_utc"],
            "observed_in_latest_sample": row["last_window"] == len(windows),
            "next_owner_action": "Controleer exacte eigenaar, lease en HOLD-bron; geen retry of claim.",
            "claim_authorized": False, "dispatch_authorized": False,
        })

    for offset, (window, _) in enumerate(windows):
        observed = set()
        for key, row in window["rows"].items():
            if row["state"] == "advisory_suggestion":
                continue
            observed.add(key)
            prior = consecutive.get(key)
            if (prior is not None and
                    prior["last_window"] == offset and
                    prior["hold_reason"] == row["reason"]):
                prior["count"] += 1
                prior["last_window"] = offset + 1
                prior["last_observed_utc"] = stamps[offset]
                prior["priority"] = row["priority"]
            else:
                if prior is not None:
                    finish_episode(prior)
                consecutive[key] = {
                    "project_id": key[0], "task_id": key[1],
                    "priority": row["priority"],
                    "hold_reason": row["reason"], "count": 1,
                    "first_window": offset + 1,
                    "first_observed_utc": stamps[offset],
                    "last_observed_utc": stamps[offset],
                    "last_window": offset + 1,
                }
        # Missing or suggestion-only is not a continuous HOLD. Retain the
        # earlier three-window episode as historical, never as a live claim.
        for key in list(consecutive):
            if key not in observed:
                finish_episode(consecutive.pop(key))
    for row in consecutive.values():
        finish_episode(row)
    persistent = sorted(completed, key=lambda e: (
        PRIORITY[e["priority"]], -e["last_window"],
        -e["consecutive_source_observations"], e["project_id"], e["task_id"]))
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
        "repeated_hold_candidates": persistent[:12],
        "repeated_hold_total": len(persistent),
        "repeated_hold_candidates_truncated": len(persistent) > 12,
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
    repeated = report.get("repeated_hold_candidates")
    if (type(repeated) is not list or len(repeated) > 12
            or type(report.get("repeated_hold_candidates_truncated")) is not bool):
        raise ValueError("unbounded repeated HOLD source")
    # An exported episode must still point at a real observed source window.
    # Otherwise a handoff could silently describe an invented "last seen".
    coverage_known = report["coverage"] in (
        "historical_sampled_series", "current_sampled_series")
    stamps = report.get("window_utc")
    if type(stamps) is not list or len(stamps) > MAX_WINDOWS:
        raise ValueError("untrusted routing source timestamp series")
    if coverage_known:
        if (len(stamps) != report["window_count"]
                or any(type(stamp) is not str
                       or not re.fullmatch(
                           r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z",
                           stamp)
                       for stamp in stamps)):
            raise ValueError("missing canonical routing window timestamps")
        prior = None
        for stamp in stamps:
            instant = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            if prior is not None and not prior < instant:
                raise ValueError("unordered routing source timestamps")
            prior = instant
        total = report.get("repeated_hold_total")
        if (type(total) is not int or not len(repeated) <= total <= 400
                or report["repeated_hold_candidates_truncated"] is not
                    (total > len(repeated))):
            raise ValueError("inconsistent historical HOLD episode coverage")
    elif repeated or report.get("repeated_hold_total") is not None:
        raise ValueError("unknown routing coverage cannot claim HOLD episodes")
    for row in repeated:
        if (type(row) is not dict or row.get("priority") not in PRIORITY
                or type(row.get("project_id")) is not str
                or not IDENT.fullmatch(row["project_id"])
                or type(row.get("task_id")) is not str
                or not IDENT.fullmatch(row["task_id"])
                or type(row.get("consecutive_source_observations")) is not int
                or not 3 <= row["consecutive_source_observations"] <= MAX_WINDOWS
                or type(row.get("first_window")) is not int
                or type(row.get("last_window")) is not int
                or not 1 <= row["first_window"] <= row["last_window"] <= report["window_count"]
                or row["last_window"] - row["first_window"] + 1 != row["consecutive_source_observations"]
                or type(row.get("observed_in_latest_sample")) is not bool
                or row["observed_in_latest_sample"] is not
                    (row["last_window"] == report["window_count"])
                or row.get("hold_reason") not in KINDS
                or KINDS[row["hold_reason"]] == "advisory_suggestion"
                or row.get("first_observed_utc") != stamps[row["first_window"] - 1]
                or row.get("last_observed_utc") != stamps[row["last_window"] - 1]
                or row.get("next_owner_action") != "Controleer exacte eigenaar, lease en HOLD-bron; geen retry of claim."
                or row.get("claim_authorized") is not False
                or row.get("dispatch_authorized") is not False):
            raise ValueError("invalid repeated owner HOLD evidence")
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



def _ordered_owner_rows(report):
    """Interleave repeated HOLDs and source changes in one strict P0-first feed."""
    rows = (
        [("REPEATED_HOLD", item) for item in report["repeated_hold_candidates"]] +
        [("SOURCE_MOVEMENT", item) for item in report["visible_events"]]
    )
    rows.sort(key=lambda pair: (
        PRIORITY[pair[1]["priority"]],
        0 if pair[0] == "REPEATED_HOLD" else 1,
        -pair[1].get("window_after", pair[1].get("last_window", report["window_count"])),
        pair[1]["project_id"],
        pair[1].get("task_id", ""),
    ))
    return rows


def owner_csv(report):
    """Portable, historical-only owner triage across all inspected windows."""
    source = _admit(report)
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow([
        "source_status", "observed_after_utc", "window_before", "window_after",
        "priority", "project_id", "task_id", "observation",
        "previous_route", "next_route", "owner_next_step",
        "consecutive_source_observations", "owner_claim_verified",
        "claim_authorized", "dispatch_authorized",
        "merge_authorized", "task_recovered_verified",
    ])
    if source["coverage"] not in ("historical_sampled_series",
                                  "current_sampled_series"):
        writer.writerow([source["coverage"], "", "", "", "", "", "", "UNKNOWN",
                         "", "", "Original sources must be rechecked; no action authorized.",
                         "", "false", "false", "false", "false", "false"])
        return output.getvalue()
    for kind, item in _ordered_owner_rows(source):
        if kind == "REPEATED_HOLD":
            writer.writerow([
                source["coverage"], item["last_observed_utc"],
                item["first_window"],
                item["last_window"], item["priority"],
                item["project_id"], item["task_id"],
                (kind if item["observed_in_latest_sample"]
                 else "REPEATED_HOLD_HISTORICAL"),
                item["hold_reason"], item["hold_reason"],
                item["next_owner_action"],
                item["consecutive_source_observations"],
                "false", "false", "false", "false", "false",
            ])
        else:
            writer.writerow([
                source["coverage"], item["source_after_utc"],
                item["window_before"], item["window_after"],
                item["priority"], item["project_id"], item["task_id"],
                item["kind"], item["before_reason"] or "",
                item["after_reason"] or "", item["next_owner_action"],
                "", "false", "false", "false", "false", "false",
            ])
    if source["repeated_hold_candidates_truncated"]:
        writer.writerow([source["coverage"], "", "", "", "", "", "", 
                         "HOLD_EPISODES_TRUNCATED_UNKNOWN", "", "",
                         "More HOLD episodes omitted; verify original windows.",
                         "", "false", "false", "false", "false", "false"])
    if source["events_truncated"]:
        writer.writerow([source["coverage"], "", "", "", "", "", "",
                         "SAMPLE_TRUNCATED_UNKNOWN", "", "",
                         "More events omitted; no source or queue all-clear.",
                         "", "false", "false", "false", "false", "false"])
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
    lines.append("HOLD-episodes: " +
                 str(len(source["repeated_hold_candidates"])) + " / " +
                 str(source["repeated_hold_total"]) + " zichtbaar; " +
                 "verdwenen uit steekproef is nooit opgelost.")
    if source["coverage"] == "historical_sampled_series":
        lines.append("HISTORISCHE REPLAY: geen actuele claim/runner/gate bewezen.")
    if any(not row["routing_inventory_complete"]
           for row in source["source_window_coverage"]):
        lines.append("Een of meer bronvensters zijn incompleet of afgekapt.")
    for kind, row in _ordered_owner_rows(source):
        if kind == "REPEATED_HOLD":
            lines.append("- HERHAALDE HOLD " + row["priority"] + " " +
                         row["project_id"] + "/" + row["task_id"] + " · " +
                         str(row["consecutive_source_observations"]) +
                         " opeenvolgende bronmomenten (" +
                         str(row["first_window"]) + "→" + str(row["last_window"]) +
                         ") · " + row["hold_reason"] +
                         " · UTC " + row["first_observed_utc"] + " → " +
                         row["last_observed_utc"] +
                         (" · HISTORISCH, niet in laatste steekproef" if
                          not row["observed_in_latest_sample"] else
                          " · ook in laatste steekproef gezien") +
                         " · eigenaar beoordeelt exact opnieuw; GEEN retry.")
        else:
            prior = row["before_reason"] or "niet eerder in steekproef"
            after = row["after_reason"] or "NIET HERWAARGENOMEN, NIET OPGELOST"
            lines.append("- " + row["priority"] + " " + row["project_id"] + "/" +
                         row["task_id"] + " · " +
                         str(row["window_before"]) + "→" +
                         str(row["window_after"]) + " · " + prior + " → " +
                         after + " · " + row["next_owner_action"])
    if source["repeated_hold_candidates_truncated"]:
        lines.append("Extra historische HOLD-episodes verborgen; geen all-clear.")
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
    for kind, row in _ordered_owner_rows(source):
        if kind == "REPEATED_HOLD":
            cards.append(
                '<article class="panel warn"><span class="tag">HERHAALDE HOLD</span><h3>' +
                escape(row["priority"]) + ' · ' + escape(row["project_id"]) +
                '/' + escape(row["task_id"]) + '</h3><p>Probleem: ' +
                escape(row["hold_reason"]) + ' · ' +
                str(row["consecutive_source_observations"]) +
                ' opeenvolgende bronwaarnemingen (venster ' +
                str(row["first_window"]) + '–' + str(row["last_window"]) +
                '); geen continu herstelbewijs.</p><p>Bron UTC: ' +
                escape(row["first_observed_utc"]) + ' → ' +
                escape(row["last_observed_utc"]) +
                '</p><p>Status: ' +
                ('OOK IN LAATSTE STEEKPROEF' if
                 row["observed_in_latest_sample"] else
                 'HISTORISCH · NIET IN LAATSTE STEEKPROEF · NIET OPGELOST') +
                '</p><p>Actie: ' + escape(row["next_owner_action"]) +
                '</p><p>Geen claim, automatische retry of vrijgave.</p></article>')
        else:
            before = row["before_reason"] or "niet in vorig bronvenster"
            after = row["after_reason"] or "niet opnieuw gezien; NIET opgelost"
            cards.append(
                '<article class="panel read"><span class="tag">' +
                escape(row["kind"]) + '</span><h3>' + escape(row["priority"]) +
                " · " + escape(row["project_id"]) + "/" + escape(row["task_id"]) +
                '</h3><p>Bron ' + str(row["window_before"]) + " → " +
                str(row["window_after"]) + " · " +
                escape(row["source_after_utc"]) +
                '</p><p>Probleem: ' + escape(before) + " → " + escape(after) +
                '</p><p>Actie: ' + escape(row["next_owner_action"]) +
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
            str(source["total_events_observed"]) + ' waargenomen overgangen · ' +
            str(len(source["repeated_hold_candidates"])) + '/' +
            str(source["repeated_hold_total"]) +
            ' herhaalde HOLD-episodes zichtbaar.</p>' +
            (''.join(cards) if cards else
             '<p>Geen verschillen in de beschikbare steekproef; '
             'niet bewezen dat alle werk is afgerond.</p>') +
            ('<p>Overige HOLD-episodes afgekapt; geen all-clear.</p>'
             if source["repeated_hold_candidates_truncated"] else "") +
            ('<p>Overige overgangen afgekapt.</p>'
             if source["events_truncated"] else "") + '</section>')

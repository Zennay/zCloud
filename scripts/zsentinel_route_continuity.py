#!/usr/bin/env python3
"""Read-only recovery routing transitions for existing private portfolio snapshots.

A disappearance from bounded samples never proves resolution, ownership
release, CI completion or permission to claim, retry, merge or dispatch.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
from pathlib import Path
from datetime import datetime, timedelta, timezone
from html import escape

SCHEMA = "zsentinel-routing-transition-v1"
IDENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
QUEUE_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
PRIORITY = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
KINDS = {
    "suggested_only_no_claim": "advisory_suggestion",
    "incident_dependency_reconciliation_required": "incident_dependency_hold",
    "required_gate_blocked": "gate_hold",
    "self_hosted_capacity_not_advisory_headroom": "gate_hold",
    "compute_destination_not_explicitly_routable": "gate_hold",
    "overlapping_suggested_scope": "shortlist_hold",
    "shortlist_limit_reached": "shortlist_hold",
}
COVERAGE = {"complete", "incomplete_or_stale"}


def _clock(value):
    if type(value) is not str or len(value) > 45:
        raise ValueError("invalid routing observation UTC")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid routing observation UTC") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("routing observation needs timezone")
    return parsed.astimezone(timezone.utc)


def _decision(snapshot):
    if type(snapshot) is not dict:
        raise ValueError("invalid private routing snapshot")
    if "source_bound_decision" not in snapshot:
        # Older handoff snapshots are not a claim source.
        return None
    portfolio = snapshot.get("portfolio")
    data = snapshot["source_bound_decision"]
    if (type(portfolio) is not dict
            or portfolio.get("schema") != "zsentinel-portfolio-overview-v1"
            or portfolio.get("queue_owner_authorized") is not False
            or portfolio.get("actions_performed") is not False
            or type(data) is not dict or type(data.get("schema_version")) is not int
            or data["schema_version"] != 1
            or data.get("mutations_performed") is not False
            or data.get("queue_claims_changed") is not False
            or data.get("ci_retried") is not False
            or data.get("deployment_authorized") is not False
            or data.get("watch_delivery_performed") is not False):
        raise ValueError("untrusted or authorizing routing snapshot")
    # Portfolio overview timestamps the SQLite queue source; routing stamps
    # the later assembled decision. They are deliberately different clocks.
    # A missing/stale SQLite source is not a false exact-timestamp mismatch.
    queue_at = portfolio.get("observed_at_utc")
    if queue_at is not None and _clock(queue_at) > _clock(data.get("observed_at_utc")) + timedelta(minutes=2):
        raise ValueError("queue observation contradicts routing source time")
    return data


def _queue_origin(snapshot, decision):
    """Require the same bounded SQLite source before comparing task identities.

    The digest is a *private source-identity receipt*, never a claim token or
    a user-facing database path. A GitHub-only/unknown source cannot certify
    that matching task slugs in two saved files refer to the same queue.
    """
    portfolio = snapshot["portfolio"]
    pressure = decision.get("live_queue_pressure")
    source_kind = portfolio.get("queue_source")
    if source_kind not in (
            "local_sqlite_transaction_query_only",
            "explicitly_unobserved_no_database"):
        raise ValueError("untrusted portfolio queue source kind")
    if (type(pressure) is not dict
            or pressure.get("claim_ownership_verified") is not False
            or pressure.get("coverage") not in (
                "current", "stale", "incomplete", "not_supplied")):
        raise ValueError("invalid private queue provenance")
    digest = pressure.get("source_file_identity_sha256_private")
    if digest is not None and (
            type(digest) is not str or not QUEUE_FINGERPRINT.fullmatch(digest)):
        raise ValueError("invalid opaque queue origin")
    if source_kind == "explicitly_unobserved_no_database":
        if digest is not None or pressure["coverage"] != "not_supplied":
            raise ValueError("GitHub-only source unexpectedly claims local queue identity")
        return None
    return digest


def _index(data):
    rows = {}
    counts = {}
    complete = data.get("routing_inventory_complete") is True
    observed = _clock(data.get("observed_at_utc"))
    for field, kind in (
        ("incident_dependency_holds", "incident_dependency_hold"),
        ("routing_gate_holds", "gate_hold"),
        ("routing_selection_holds", "shortlist_hold"),
    ):
        receipt = data.get(field)
        if receipt is None:
            complete = False
            counts[kind] = None
            continue
        if (type(receipt) is not dict
                or receipt.get("coverage") not in COVERAGE
                or type(receipt.get("count")) is not int
                or not 0 <= receipt["count"] <= 250
                or type(receipt.get("examples")) is not list
                or len(receipt["examples"]) > 8
                or len(receipt["examples"]) > receipt["count"]
                or receipt.get("samples_truncated") is not
                    (receipt["count"] > len(receipt["examples"]))
                or receipt.get("claim_authorized") is not False
                or (kind != "incident_dependency_hold"
                    and receipt.get("dispatch_authorized") is not False)):
            raise ValueError("untrusted routing HOLD source")
        if receipt["coverage"] != "complete":
            complete = False
        counts[kind] = receipt["count"] if receipt["coverage"] == "complete" else None
        for row in receipt["examples"]:
            _insert(rows, row, kind)
    offered = data.get("independent_task_suggestions")
    if type(offered) is not list or len(offered) > 5:
        raise ValueError("untrusted routing shortlist")
    for row in offered:
        _insert(rows, row, "advisory_suggestion")
    counts["advisory_suggestion"] = len(offered) if complete else None
    # Any truncated HOLD category can hide identities and changes. Never
    # mistake the visible subset for a complete routing inventory.
    truncated = any(type(data.get(field)) is dict and
                    data[field].get("samples_truncated") is True
                    for field in ("incident_dependency_holds",
                                  "routing_gate_holds", "routing_selection_holds"))
    return {"observed": observed, "rows": rows, "counts": counts,
            # Inventory coverage and *display-sample* coverage are separate:
            # a complete underlying task snapshot can still hide examples.
            "complete": complete and not truncated,
            "sample_truncated": truncated}


def _insert(index, row, category):
    if type(row) is not dict:
        raise ValueError("invalid routing transition item")
    pid, ident = row.get("project_id"), row.get("id")
    tier = row.get("priority")
    if (type(pid) is not str or not IDENT.fullmatch(pid)
            or type(ident) is not str or not IDENT.fullmatch(ident)
            or tier not in PRIORITY):
        raise ValueError("invalid routing transition identity")
    key = (pid, ident)
    if key in index:
        raise ValueError("conflicting routing states for same task")
    reason = row.get("reason") if category != "advisory_suggestion" else "suggested_only_no_claim"
    if reason not in KINDS or KINDS[reason] != category:
        raise ValueError("invalid routing transition reason")
    if category == "advisory_suggestion":
        if row.get("status") != "suggested_only_no_claim":
            raise ValueError("unexpected routing suggestion status")
    elif row.get("claim_authorized") is not False:
        raise ValueError("HOLD is not a claim")
    if category in ("gate_hold", "shortlist_hold") and row.get("dispatch_authorized") is not False:
        raise ValueError("HOLD is not dispatch authority")
    index[key] = {"project_id": pid, "id": ident, "priority": tier,
                  "state": category, "reason": reason,
                  "claim_authorized": False, "dispatch_authorized": False}


def compare(previous, current, *, now=None, allow_historical=False):
    if type(allow_historical) is not bool:
        raise ValueError("historical comparison needs explicit boolean")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("timezone-aware comparison time required")
    newer = _decision(current)
    if newer is None:
        raise ValueError("current report must include source-bound routing")
    new = _index(newer)
    if new["observed"] > now + timedelta(minutes=2):
        raise ValueError("future current routing observation")
    # The standalone offline export always describes saved history, even
    # when a file is only seconds old. It has NO independent live claim check.
    source_is_historical = (allow_historical or
                            now - new["observed"] > timedelta(minutes=10))
    if source_is_historical and not allow_historical:
        # An archived JSON export may be valid history but not a live
        # operator task transition. Do not present old state as new routing.
        return _result("current_routing_source_stale", new,
                       previous_at=None, fresh=False)
    older = _decision(previous)
    if older is None:
        return _result("previous_routing_not_supplied", new,
                       previous_at=None, fresh=False)
    old = _index(older)
    previous_origin = _queue_origin(previous, older)
    current_origin = _queue_origin(current, newer)
    if (previous_origin is None or current_origin is None
            or previous_origin != current_origin):
        # A same-looking task ID is not stable evidence across different
        # queue databases, or where the source database was never observed.
        return _result("queue_source_identity_unknown_or_changed", new,
                       previous_at=old["observed"], fresh=False)
    if (old["observed"] >= new["observed"]
            or old["observed"] < new["observed"] - timedelta(hours=72)):
        return _result("previous_routing_too_old_or_out_of_order", new,
                       previous_at=old["observed"], fresh=False)
    # A saved report is only a source snapshot, never proof of current
    # ownership. Comparisons are still informative when a source is partial,
    # but all disappearances must remain explicitly not-reobserved.
    created, persisting, transitioned, missing = [], [], [], []
    for key, value in sorted(new["rows"].items(), key=lambda pair: (
            PRIORITY[pair[1]["priority"]], pair[0])):
        prior = old["rows"].get(key)
        if prior is None:
            created.append(value)
        elif (prior["state"], prior["reason"], prior["priority"]) != (
                value["state"], value["reason"], value["priority"]):
            transitioned.append({
                "project_id": key[0], "id": key[1],
                "priority": value["priority"],
                "before": prior["state"], "after": value["state"],
                "before_reason": prior["reason"], "after_reason": value["reason"],
                "owner_recheck_required": True, "claim_authorized": False,
            })
        else:
            persisting.append(value)
    for key, value in sorted(old["rows"].items(), key=lambda pair: (
            PRIORITY[pair[1]["priority"]], pair[0])):
        if key not in new["rows"]:
            missing.append(value)
    return {
        **_result(("historical_bounded_source_comparison" if source_is_historical
                   else "bounded_source_comparison"), new,
                  previous_at=old["observed"], fresh=not source_is_historical),
        "prior_coverage_complete": old["complete"],
        "previous_source_examples_truncated": old["sample_truncated"],
        "same_queue_origin_verified": True,
        "newly_observed": created[:12],
        "unchanged_observed": persisting[:12],
        "state_transitions": transitioned[:12],
        "not_reobserved_not_resolved": missing[:12],
        "newly_observed_count": len(created),
        "state_transition_count": len(transitioned),
        "not_reobserved_count": len(missing),
        "samples_truncated": any(len(rows) > 12 for rows in
                                 (created, persisting, transitioned, missing)),
        "task_released_verified": False,
        "issue_closed_verified": False,
        "owner_claim_verified": False,
        "merge_authorized": False,
        "actions_performed": False,
    }


def _result(coverage, newer, previous_at, fresh):
    stamp = lambda value: value.isoformat(timespec="seconds").replace("+00:00", "Z") if value else None
    return {
        "schema": SCHEMA, "coverage": coverage,
        "observed_at_utc": stamp(newer["observed"]),
        "previous_observed_at_utc": stamp(previous_at),
        "current_coverage_complete": newer["complete"],
        "current_source_recent_at_comparison": fresh,
        "same_queue_origin_verified": False,
        "current_source_examples_truncated": newer["sample_truncated"],
        "current_state_counts": newer["counts"],
        "newly_observed": [], "unchanged_observed": [],
        "state_transitions": [], "not_reobserved_not_resolved": [],
        "newly_observed_count": None, "state_transition_count": None,
        "not_reobserved_count": None, "samples_truncated": False,
        "prior_coverage_complete": False,
        "previous_source_examples_truncated": False,
        "task_released_verified": False, "issue_closed_verified": False,
        "owner_claim_verified": False, "merge_authorized": False,
        "actions_performed": False,
    }


def _ordered_owner_events(report):
    """One deterministic P0-first owner queue shared by text, HTML and CSV.

    A change type is not an execution gate. Prioritize the *source task*
    priority first, then a changed route, newly seen, missing, unchanged.
    No list entry grants ownership or means an issue was fixed.
    """
    types = (
        ("ROUTE_CHANGED", "state_transitions", 0,
         "Controleer actuele eigenaar, claim/lease, bestaande PR en uitvoeringsgate."),
        ("NEW_IN_SAMPLE", "newly_observed", 1,
         "Controleer claim en bestandsscope voordat werk start."),
        ("NOT_REOBSERVED_NOT_RESOLVED", "not_reobserved_not_resolved", 2,
         "Controleer originele bron; een ontbrekend voorbeeld is NIET opgelost."),
        ("UNCHANGED", "unchanged_observed", 3,
         "Geen herhaalde herstelactie zonder nieuwe broninformatie."),
    )
    events = []
    for kind, field, order, action in types:
        rows = report.get(field)
        if type(rows) is not list or len(rows) > 12:
            raise ValueError("unbounded routing owner events")
        for row in rows:
            if (type(row) is not dict
                    or row.get("priority") not in PRIORITY
                    or type(row.get("project_id")) is not str
                    or not IDENT.fullmatch(row["project_id"])
                    or type(row.get("id")) is not str
                    or not IDENT.fullmatch(row["id"])):
                raise ValueError("untrusted routing owner event")
            if kind == "ROUTE_CHANGED":
                before, after = row.get("before_reason"), row.get("after_reason")
            else:
                reason = row.get("reason")
                before = (reason if kind in (
                    "NOT_REOBSERVED_NOT_RESOLVED", "UNCHANGED") else "")
                after = (reason if kind in (
                    "NEW_IN_SAMPLE", "UNCHANGED") else "")
            if any(type(reason) is not str or reason not in KINDS
                   for reason in (before, after) if reason):
                raise ValueError("untrusted routing owner transition reason")
            events.append({
                "event": kind, "sort_order": order, "priority": row["priority"],
                "project_id": row["project_id"], "id": row["id"],
                "before": before, "after": after, "action": action,
            })
    return sorted(events, key=lambda row: (
        PRIORITY[row["priority"]], row["sort_order"],
        row["project_id"], row["id"]))


def as_text(report):
    if (type(report) is not dict or report.get("schema") != SCHEMA
            or report.get("actions_performed") is not False
            or report.get("merge_authorized") is not False):
        raise ValueError("untrusted routing comparison")
    lines = ["zSentinel · routeringsveranderingen (alleen advies)"]
    if report["coverage"] not in ("bounded_source_comparison",
                                   "historical_bounded_source_comparison"):
        if report["coverage"] == "queue_source_identity_unknown_or_changed":
            lines.append("Queue-oorsprong ONBEKEND of verschillend: geen taak-ID-overgangen afleidbaar.")
        else:
            lines.append("Vorige routeringsbron ontbreekt of is onvergelijkbaar; veranderingen ONBEKEND.")
        return "\n".join(lines)
    if report["coverage"] == "historical_bounded_source_comparison":
        lines.append("HISTORISCHE REPLAY: huidige claim/CI/lease en echte routing zijn ONBEKEND; niet uitvoeren.")
    lines.append("Bronmomenten: " + report["previous_observed_at_utc"] +
                 " → " + report["observed_at_utc"])
    lines.append("Nieuwe waarnemingen: " + str(report["newly_observed_count"]) +
                 " · gewijzigde route: " + str(report["state_transition_count"]) +
                 " · niet meer in steekproef: " + str(report["not_reobserved_count"]))
    if not report["prior_coverage_complete"] or not report["current_coverage_complete"]:
        lines.append("Bron ONVOLLEDIG; aantallen zijn geen volledig werkoverzicht.")
    for event in _ordered_owner_events(report):
        transition = (event["before"] + " → " + event["after"]
                      if event["before"] and event["after"]
                      and event["before"] != event["after"]
                      else event["after"] or event["before"])
        lines.append("- " + event["priority"] + " " + event["event"] + " " +
                     event["project_id"] + "/" + event["id"] + " · " +
                     transition + " · " + event["action"] + " Geen claim/dispatch.")
    if report["samples_truncated"] or report["current_source_examples_truncated"] or report["previous_source_examples_truncated"]:
        lines.append("Voorbeelden afgekapt; verwijderde of ongetoonde taken blijven ONBEKEND.")
    return "\n".join(lines)


def as_html(report):
    """Operator cards using the same bounded transition decision as text/JSON."""
    visible = as_text(report)
    if report["coverage"] not in ("bounded_source_comparison",
                                  "historical_bounded_source_comparison"):
        return ('<section class="panel warn"><h2>Routeringsveranderingen · ONBEKEND</h2>'
                '<p>' + escape(visible.splitlines()[-1]) +
                '</p><p>Geen claim, vrijgave of herstelauthorisatie.</p></section>')
    historic = report["coverage"] == "historical_bounded_source_comparison"
    cards = []
    for event in _ordered_owner_events(report):
        before = event["before"]
        after = event["after"]
        route = (before + " → " + after if before and after and before != after
                 else after or before)
        cards.append(
            '<article class="panel' +
            (' warn' if event["event"] == "NOT_REOBSERVED_NOT_RESOLVED" else
             ' read') + '"><span class="tag">' +
            escape(event["event"]) + '</span><h3>' +
            escape(event["priority"]) + ' · ' +
            escape(event["project_id"]) + ' / ' + escape(event["id"]) +
            '</h3><p>Probleem: ' + escape(route) +
            '</p><p>Actie: ' + escape(event["action"]) +
            '</p><p>Status: Alleen bronsignaal, geen claim, retry, merge of deploy.</p></article>'
        )
    panels = "".join(cards)
    note = ('HISTORISCHE REPLAY: geen actuele claim- of leasebron. '
            if historic else 'Vers assembleert tijdvenster; nog altijd slechts bronadvies. ')
    if (not report["prior_coverage_complete"]
            or not report["current_coverage_complete"]):
        note += "Broninventaris ONVOLLEDIG of steekproef afgekapt. "
    note += ("Verdwenen taken zijn niet vrijgegeven; bronidentiteit is "
             "alleen voor vergelijking geverifieerd. Geen automatische herstelactie.")
    return ('<section class="panel read" aria-label="Routeringsveranderingen">'
            '<h2>Routeringsveranderingen · P0-eerst</h2><p class="muted">' +
            escape(note) + '</p><p>Bronmomenten: ' +
            escape(report["previous_observed_at_utc"]) + ' → ' +
            escape(report["observed_at_utc"]) + '</p>' +
            (panels if panels else
             '<p>Geen zichtbare overgang in dit beperkte bronvenster; '
             'geen bewijs dat alle taken vrij zijn.</p>') + '</section>')


def as_csv(report):
    """Create a private importable owner queue, never a task/claim instruction.

    All exported columns have bounded values from the admitted report; status
    and owner action make historical and unobserved tasks non-executable.
    """
    as_text(report)
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow([
        "source_kind", "source_previous_utc", "source_current_utc",
        "queue_origin_verified", "source_inventory_complete",
        "observation", "priority", "project_id", "task_id",
        "previous_route", "current_route", "owner_next_action",
        "claim_authorized", "dispatch_authorized",
        "merge_authorized", "task_released_verified",
    ])
    kind = report["coverage"]
    comparison = kind in ("bounded_source_comparison",
                          "historical_bounded_source_comparison")
    base = [
        kind, report["previous_observed_at_utc"] or "",
        report["observed_at_utc"] or "",
        "false" if not report["same_queue_origin_verified"] else "true",
        ("true" if report["prior_coverage_complete"] and
         report["current_coverage_complete"] else "false"),
    ]
    if not comparison:
        writer.writerow([*base, "UNKNOWN", "", "", "", "", "",
                         "Refresh original sources; no current claim or recovery proof.",
                         "false", "false", "false", "false"])
        return output.getvalue()
    for event in _ordered_owner_events(report):
        writer.writerow([
            *base, event["event"], event["priority"], event["project_id"],
            event["id"], event["before"], event["after"], event["action"] +
            (" HISTORICAL REPLAY ONLY." if
             report["coverage"] == "historical_bounded_source_comparison" else ""),
            "false", "false", "false", "false",
        ])
    if report["samples_truncated"] or report["current_source_examples_truncated"] or report["previous_source_examples_truncated"]:
        writer.writerow([*base, "SAMPLE_TRUNCATED_UNKNOWN", "", "", "", "",
                         "", "Additional tasks outside bounded samples remain UNKNOWN.",
                         "false", "false", "false", "false"])
    return output.getvalue()


def main(argv=None):
    """Standalone offline operator comparison; never fetch live data or mutate it."""
    parser = argparse.ArgumentParser(description="zSentinel privé routing snapshot comparison (read-only)")
    parser.add_argument("--previous", required=True, type=Path,
                        help="existing private 0600 portfolio JSON snapshot")
    parser.add_argument("--current", required=True, type=Path,
                        help="later saved private portfolio JSON snapshot")
    parser.add_argument("--format", choices=("json", "html", "text", "csv"), default="html")
    parser.add_argument("--output", type=Path, required=True,
                        help="new create-only private .json, .html or .md file")
    args = parser.parse_args(argv)
    try:
        paths = [args.previous.resolve(), args.current.resolve(), args.output.resolve()]
        if len(set(paths)) != 3:
            raise ValueError("private source and output files must be distinct")
        from zsentinel_recovery_router import read_json
        from zsentinel_private_output import write_private_output
        # The standalone operator path handles PRIVATE owner dossiers, not
        # public JSON fixtures. Verify the opened inodes and 0600-equivalent
        # permissions, not merely the filename or a pre-open pathname stat.
        bound = 6 * 1024 * 1024
        before = read_json(args.previous, bound, "previous private portfolio",
                           private_owner_only=True)
        after = read_json(args.current, bound, "current private portfolio",
                          private_owner_only=True)
        # Historical-only is an explicit request in this OFFLINE command.
        # Never present an archived comparison as current admission.
        result = compare(before, after, allow_historical=True)
        if args.format == "json":
            body = json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2)
            suffix = "json"
        elif args.format == "html":
            body = ('<!doctype html><html lang="nl"><head><meta charset="utf-8">'
                    '<meta name="referrer" content="no-referrer">'
                    '<meta http-equiv="Content-Security-Policy" '
                    'content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'">'
                    '<meta name="viewport" content="width=device-width,initial-scale=1">'
                    '<title>zSentinel routing bronvergelijking</title>'
                    '<style>:root{color-scheme:dark}*{box-sizing:border-box}'
                    'body{font:1rem/1.5 system-ui;background:#111c2c;color:#f2f6fc;'
                    'max-width:980px;margin:auto;padding:clamp(1rem,4vw,2.5rem)}'
                    'h2{font-size:1.35rem}h3{font-size:1.05rem;margin:.35rem 0}'
                    '.panel{padding:1rem;margin:.7rem 0;border:1px solid #526176;'
                    'border-radius:10px;background:#192a3b}'
                    '.panel.warn{border-left:5px solid #eab16b}'
                    '.panel.read{border-left:5px solid #94c9e7}'
                    '.tag{display:inline-block;border:1px solid #abc1d0;'
                    'border-radius:20px;padding:.15rem .55rem;font-size:.75rem}'
                    '.muted{color:#b5cadb}p{overflow-wrap:anywhere}'
                    'li{margin:.4rem}</style>'
                    '</head><body><main>' + as_html(result) + '</main></body></html>')
            suffix = "html"
        elif args.format == "csv":
            body = as_csv(result)
            suffix = "csv"
        else:
            body = as_text(result)
            suffix = "md"
        write_private_output(args.output, body, suffix)
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        parser.exit(2, "zSentinel offline routing comparison unavailable: " + str(exc) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

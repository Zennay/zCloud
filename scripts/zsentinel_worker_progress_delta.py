#!/usr/bin/env python3
"""Compare two bounded, owner-neutral worker event snapshots (#1342).

A heartbeat and a generation-progress cursor have distinct meanings.
Neither is a product commit, an active claim lease, or a recovery receipt.
All inputs are private observations from one indexed local history.db.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

from zsentinel_disk_trend import load_previous
from zsentinel_private_output import write_private_output
from zsentinel_worker_progress_observer import observe_worker_progress

SCHEMA = "zsentinel-worker-progress-delta-v1"
SOURCE_SCHEMA = "zsentinel-worker-progress-observation-v1"
SOURCE = "existing_sqlite_mode_ro_query_only_indexed_runner_events"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
SLUG = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
MAX_ENTRIES = 24
MAX_HOURS = 72


def _utc(value):
    if type(value) is not str or not 1 <= len(value) <= 45:
        raise ValueError("worker source UTC clock required")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid worker UTC clock") from exc
    if dt.tzinfo is None or dt.utcoffset() != timedelta(0):
        raise ValueError("worker source clock must be UTC")
    return dt.astimezone(timezone.utc)


def _cursor(value):
    if value is None:
        return None
    if type(value) is not int or not 1 <= value <= 2**53 - 1:
        raise ValueError("invalid indexed worker cursor")
    return value


def _source(r):
    if (type(r) is not dict or r.get("schema") != SOURCE_SCHEMA
            or r.get("source") != SOURCE
            or r.get("actions_performed") is not False
            or r.get("worker_resumed_verified") is not False
            or r.get("live_worker_claims_verified") is not False
            or r.get("database_write_authorized") is not False
            or r.get("runner_restart_authorized") is not False
            or type(r.get("event_inventory_complete")) is not bool
            or r["event_inventory_complete"] is not False):
        raise ValueError("untrusted read-only worker evidence")
    identity = r.get("source_file_identity_sha256_private")
    if type(identity) is not str or not HEX64.fullmatch(identity):
        raise ValueError("worker source identity missing")
    projects = r.get("selected_projects")
    rows = r.get("entries")
    if (type(projects) is not list or not 1 <= len(projects) <= 3
            or len(set(projects)) != len(projects)
            or any(type(p) is not str or not SLUG.fullmatch(p) for p in projects)
            or type(rows) is not list or len(rows) > MAX_ENTRIES):
        raise ValueError("unbounded or untrusted worker projects")
    observed = {}
    for row in rows:
        if type(row) is not dict:
            raise ValueError("untrusted worker row")
        project, slot = row.get("project_id"), row.get("worker_slot")
        if (project not in projects or type(slot) is not int
                or not 1 <= slot <= 16 or (project,slot) in observed
                or row.get("owner_claim_verified") is not False
                or row.get("worker_resumed_verified") is not False
                or row.get("repair_authorized") is not False
                or row.get("event_source") != "indexed_local_runner_events_only"):
            raise ValueError("untrusted worker slot or authorization flag")
        last = _cursor(row.get("latest_indexed_event_id"))
        progress = _cursor(row.get("latest_indexed_progress_event_id"))
        if progress is not None and (last is None or progress > last):
            raise ValueError("progress event cannot be after last event")
        state = row.get("desired_state")
        if state not in ("running", "paused", "draining", "unknown"):
            raise ValueError("unknown worker desired state")
        allocated = row.get("allocated_global_slot")
        if allocated is not None and (type(allocated) is not int or
                                      not 1 <= allocated <= 32):
            raise ValueError("invalid global worker slot")
        observed[(project,slot)] = {
            "latest": last, "progress": progress, "desired": state,
            "allocated": allocated,
            "claim_from_source": row.get("queue_claim_status_observed")
                                 in ("claimed", "running", "verifying"),
            "claim_lease_current_from_source": row.get("queue_lease_current_from_source") is True,
        }
    return identity, _utc(r.get("observed_at_utc")), tuple(projects), observed


def compare(previous, current, now=None):
    key0, t0, projects0, old = _source(previous)
    key1, t1, projects1, new = _source(current)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("comparison clock must be timezone-aware")
    now = now.astimezone(timezone.utc)
    if (key0 != key1 or projects0 != projects1
            or not timedelta(0) < t1 - t0 <= timedelta(hours=MAX_HOURS)
            or t1 > now + timedelta(minutes=2)):
        raise ValueError("same live indexed SQLite and selected projects, ordered UTC snapshots required")
    items = []
    counts = {
        "generation_progress_signal_observed": 0,
        "new_events_without_indexed_progress": 0,
        "running_allocated_no_new_event": 0,
        "paused_or_draining_owner_hold": 0,
        "indexed_source_reset_or_regression": 0,
        "new_in_bounded_sample_not_new_claim": 0,
        "missing_in_current_sample_not_resolved": 0,
        "other_bounded": 0,
    }
    # Both sides of the bounded sample matter. Previously only the intersection
    # was rendered: a worker that disappeared from observation had no private
    # owner-action row at all, despite appearing in an aggregate counter.
    # Such a disappearance is NEVER evidence that the task was completed,
    # the claim was released or its original worker can be replaced.
    shared = old.keys() & new.keys()
    for project, slot in sorted(old.keys() | new.keys()):
        before, after = old.get((project, slot)), new.get((project, slot))
        first = before["latest"] if before is not None else None
        last = after["latest"] if after is not None else None
        pfirst = before["progress"] if before is not None else None
        plast = after["progress"] if after is not None else None
        if before is None:
            stage = "new_in_bounded_sample_not_new_claim"
            action = "Controleer met eigenaar of dit een bestaande toewijzing is; geen claim of vrije capaciteit bewezen."
        elif after is None:
            stage = "missing_in_current_sample_not_resolved"
            action = "Controleer bij eigenaar waarom de worker niet meer wordt waargenomen; geen herstel of vrijgave bewezen."
        elif ((first is not None and last is not None and last < first)
                or (pfirst is not None and plast is not None and plast < pfirst)
                or (pfirst is not None and plast is None)):
            stage = "indexed_source_reset_or_regression"
            action = "Eigenaar verifieert reset/retentie en broncontinuïteit; geen hervatting afleiden."
        elif after["desired"] in ("paused", "draining"):
            stage = "paused_or_draining_owner_hold"
            action = "Respecteer actuele pauze-/drainstatus en bestaande eigenaar."
        elif plast is not None and (pfirst is None or plast > pfirst):
            stage = "generation_progress_signal_observed"
            action = "Controleer echte codecommit of taakoplevering; event is geen productbewijs."
        elif last is not None and (first is None or last > first):
            stage = "new_events_without_indexed_progress"
            action = "Er kwamen events bij, maar geen nieuw expliciet progress-event; inspecteer eigenaarstatus."
        elif (after["desired"] == "running" and after["allocated"] is not None
              and t1 - t0 >= timedelta(minutes=15)):
            stage = "running_allocated_no_new_event"
            action = "Vraag eigenaar om actuele lease en echte voortgang te verifiëren; geen takeover."
        else:
            stage = "other_bounded"
            action = "Deze twee snapshots bewijzen geen stagnatie of productoplevering."
        counts[stage] += 1
        items.append({
            "project_id": project, "worker_slot": slot,
            "stage": stage, "owner_action": action,
            "event_cursor_advanced_observed": (
                last is not None and (first is None or last > first)),
            "explicit_progress_cursor_advanced_observed": (
                plast is not None and (pfirst is None or plast > pfirst)),
            "current_desired_state": after["desired"] if after is not None else "not_observed",
            "current_global_slot_observed": after["allocated"] if after is not None else None,
            "source_claim_lease_current_but_not_authority":
                after["claim_lease_current_from_source"] if after is not None else False,
            "slot_present_in_previous_sample": before is not None,
            "slot_present_in_current_sample": after is not None,
            "claim_epoch_verified": False,
            "product_delivery_verified": False,
            "worker_recovery_verified": False,
            "owner_claim_changed": False,
        })
    return {
        "schema": SCHEMA,
        "previous_observed_at_utc": t0.isoformat(),
        "current_observed_at_utc": t1.isoformat(),
        "source_coverage": "same_indexed_db_two_bounded_snapshots_not_claim_epoch",
        "source_selected_projects": list(projects1),
        "shared_worker_slots_observed": len(shared),
        "new_worker_slots_unpaired": len(new.keys() - old.keys()),
        "disappeared_worker_slots_unpaired": len(old.keys() - new.keys()),
        "counts": counts,
        "items": items,
        "event_is_not_product_delivery": True,
        "claim_epoch_verified": False,
        "task_completion_verified": False,
        "owner_claims_changed": False,
        "queue_write_authorized": False,
        "restart_authorized": False,
        "automatic_followup_dispatched": False,
    }


def public_summary(report):
    if (type(report) is not dict or report.get("schema") != SCHEMA
            or report.get("event_is_not_product_delivery") is not True
            or any(report.get(k) is not False for k in (
                "claim_epoch_verified", "task_completion_verified",
                "owner_claims_changed", "queue_write_authorized",
                "restart_authorized", "automatic_followup_dispatched"))
            or type(report.get("counts")) is not dict):
        raise ValueError("unsafe worker progress trend")
    return {
        "schema": "zsentinel-worker-progress-delta-public-v1",
        "coverage": report["source_coverage"],
        "shared_worker_slots": report["shared_worker_slots_observed"],
        "progress_signals_observed": report["counts"]["generation_progress_signal_observed"],
        "new_event_without_progress": report["counts"]["new_events_without_indexed_progress"],
        "running_without_new_events": report["counts"]["running_allocated_no_new_event"],
        "owner_paused_or_draining": report["counts"]["paused_or_draining_owner_hold"],
        "source_regressions": report["counts"]["indexed_source_reset_or_regression"],
        "new_in_sample_not_new_claim": report["new_worker_slots_unpaired"],
        "missing_in_sample_not_recovered": report["disappeared_worker_slots_unpaired"],
        "product_delivery_verified": False,
        "claim_epoch_verified": False,
        "claims_changed": False,
        "recovery_authorized": False,
    }


def human_text(report):
    data = public_summary(report)
    return (
        "zSentinel workers #1342 · " +
        str(data["progress_signals_observed"]) +
        " expliciete voortgangssignalen, " +
        str(data["new_event_without_progress"]) +
        " workers met nieuwe events maar geen nieuw progress-event, " +
        str(data["running_without_new_events"]) +
        " toegewezen workers zonder eventtoename; " +
        str(data["new_in_sample_not_new_claim"]) +
        " nieuw in steekproef (geen nieuwe claim), " +
        str(data["missing_in_sample_not_recovered"]) +
        " niet meer in steekproef (geen herstelbewijs).\n"
        "ACTIE: eigenaar verifieert claim-epoch, lease en echte productoplevering; "
        "geen heartbeat- of eventbewijs voor commit.\n"
        "STATUS: brongebonden eventvergelijking; geen claim-, herstel- of releasebevoegdheid."
    )


def public_html(report):
    data = public_summary(report)
    return (
        '<section aria-label="Worker voortgang tussen twee meetpunten">'
        '<h2>Worker · heartbeat versus echte events (#1342)</h2>'
        '<p>Nieuwe expliciete generatiesignalen: ' +
        str(data["progress_signals_observed"]) +
        '; alleen andere/heartbeat events: ' + str(data["new_event_without_progress"]) +
        '; toegewezen running zonder nieuwe events: ' +
        str(data["running_without_new_events"]) +
        '; nieuw in beperkte steekproef (geen nieuwe claim): ' +
        str(data["new_in_sample_not_new_claim"]) +
        '; verdwenen uit beperkte steekproef (geen herstel): ' +
        str(data["missing_in_sample_not_recovered"]) +
        ' (bounded bron; GEEN bewijs van falen of voortgang).</p>'
        '<p>Claim-epoch en concrete productoplevering NIET bewezen; '
        'geen automatische overname of herstart.</p>'
        '<p><a href="https://github.com/Zennay/zCloud/issues/1342" '
        'rel="noopener noreferrer">Open issue #1342</a></p></section>'
    )



def private_markdown(report):
    """Human-readable bounded owner handoff, including unpaired worker slots.

    The Markdown is a private offline observation, never a claim, recovery,
    GitHub approval or proof that a disappearing worker completed its task.
    """
    data = public_summary(report)
    rows = report.get("items")
    if type(rows) is not list or len(rows) > 2 * MAX_ENTRIES:
        raise ValueError("unbounded worker owner handoff")
    lines = [
        "# zSentinel · worker voortgang en eigenaaracties",
        "",
        "**Bron:** twee read-only observaties van dezelfde geïndexeerde DB; "
        "geen claim-epoch, productoplevering, veilige vrijgave of herstel bewezen.",
        "",
        "- Vorige bron: " + report["previous_observed_at_utc"],
        "- Huidige bron: " + report["current_observed_at_utc"],
        "- Beide keren waargenomen slots: " + str(data["shared_worker_slots"]),
        "- Nieuw in beperkte bron (niet nieuwe claim): " +
        str(data["new_in_sample_not_new_claim"]),
        "- Verdwenen uit beperkte bron (niet opgelost): " +
        str(data["missing_in_sample_not_recovered"]),
        "",
        "| Project | Worker-slot | Waarneming | Bevoegde eigenaaractie |",
        "| --- | ---: | --- | --- |",
    ]
    for row in rows:
        if (type(row) is not dict
                or type(row.get("project_id")) is not str
                or not SLUG.fullmatch(row["project_id"])
                or type(row.get("worker_slot")) is not int
                or not 1 <= row["worker_slot"] <= 16
                or type(row.get("stage")) is not str
                or row["stage"] not in report["counts"]
                or type(row.get("owner_action")) is not str
                or not 1 <= len(row["owner_action"]) <= 220
                or any(ord(c) < 32 or ord(c) == 127 for c in row["owner_action"])):
            raise ValueError("invalid private worker followup")
        # Reject Markdown table injection even though current actions are
        # internally generated and project/slot identities are canonical.
        label = row["stage"].replace("|", "\\|")
        action = row["owner_action"].replace("|", "\\|")
        lines.append("| " + row["project_id"] + " | " +
                     str(row["worker_slot"]) + " | " + label +
                     " | " + action + " |")
    lines += ["", "**Vervolg:** bevestig bij de werkelijke eigenaar of dezelfde "
              "worker, taak, lease en codelevering nog bestaan. Geen automatische "
              "claim, retry, dispatch, restart, merge of deploy.", ""]
    return "\n".join(lines)


def private_html(report):
    public_summary(report)
    rows = []
    for item in report["items"]:
        rows.append('<tr><td>' + escape(item["project_id"]) + '</td><td>' +
                    str(item["worker_slot"]) + '</td><td>' +
                    escape(item["stage"]) + '</td><td>' +
                    escape(item["owner_action"]) + '</td></tr>')
    return (
        '<!doctype html><html lang="nl"><meta charset="utf-8">'
        '<meta name="referrer" content="no-referrer">'
        '<meta http-equiv="Content-Security-Policy" content="default-src &#39;none&#39;;'
        'style-src &#39;unsafe-inline&#39;;base-uri &#39;none&#39;">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>zSentinel · Worker voortgang</title>'
        '<style>body{font:16px system-ui;max-width:1000px;margin:1rem auto;'
        'background:#0b1220;color:#f8fafc;line-height:1.5;padding:1rem}'
        'a{color:#93c5fd}table{border-collapse:collapse;width:100%}'
        'td,th{border:1px solid #64748b;padding:.5rem}</style>'
        '<main><h1>zSentinel · Worker progress tussen twee waarnemingen</h1>' +
        public_html(report) + '<table><thead><tr><th>Project</th>'
        '<th>Slot</th><th>Bronstatus</th><th>Benodigde eigenaaractie</th>'
        '</tr></thead><tbody>' + "".join(rows) +
        '</tbody></table></main></html>'
    )


def main():
    parser = argparse.ArgumentParser(description="Private read-only worker event snapshots and cross-snapshot progress")
    sources = parser.add_mutually_exclusive_group(required=True)
    sources.add_argument("--database", type=Path, help="existing indexed local history.db only")
    sources.add_argument("--current-snapshot", type=Path,
                         help="already captured 0600 private worker source; compare without database")
    parser.add_argument("--project", action="append", default=[],
                        help="one to three canonical project IDs")
    parser.add_argument("--previous", type=Path)
    parser.add_argument("--snapshot-output", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--format", choices=("text", "html", "json", "md"), default="text")
    args = parser.parse_args()
    try:
        if args.current_snapshot and (not args.previous or args.snapshot_output):
            raise ValueError("offline requires existing previous and current private snapshots")
        if args.database and not args.project:
            raise ValueError("read-only database observation needs 1-3 selected projects")
        if args.current_snapshot and args.project:
            raise ValueError("offline projects are bound to existing saved observations")
        if not args.previous and not args.snapshot_output:
            raise ValueError("choose private snapshot output or previous comparison")
        if args.output and (args.format == "text" or not args.previous):
            raise ValueError("private trend output needs previous and html/json/md format")
        if args.previous and args.format != "text" and not args.output:
            raise ValueError("non-text comparison requires new private report file")
        chosen = [p for p in (args.output, args.snapshot_output, args.previous,
                              args.current_snapshot) if p]
        if len({str(p.resolve()) for p in chosen}) != len(chosen):
            raise ValueError("private source/output paths must all differ")
        latest = (load_previous(args.current_snapshot) if args.current_snapshot
                  else observe_worker_progress(args.database, args.project))
        result = (compare(load_previous(args.previous), latest)
                  if args.previous else None)
        if result and args.output:
            content = (private_html(result) if args.format == "html"
                       else json.dumps(result, ensure_ascii=True, indent=2)
                       if args.format == "json" else private_markdown(result)
                       if args.format == "md" else human_text(result))
            write_private_output(args.output, content, args.format)
        if args.snapshot_output:
            write_private_output(args.snapshot_output,
                                 json.dumps(latest, ensure_ascii=True, sort_keys=True, indent=2),
                                 "json")
        print(human_text(result) if result and args.format == "text" else
              "Private worker source/report generated without runtime, queue or claim changes.")
    except (ValueError, OSError, json.JSONDecodeError):
        parser.exit(2, "zSentinel worker progress source unavailable; no actions made.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Read-only #677 portfolio queue claim-attempt trend and owner handoff.

Compares two private, bounded snapshots of ONE source SQLite file. Attempts
are claims, not failed executions. It never retries work or changes ownership.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

from zsentinel_disk_trend import load_previous
from zsentinel_private_output import write_private_output
from zsentinel_queue_observer import observe

SCHEMA = "zsentinel-queue-claim-trend-v1"
QUEUE_SCHEMA = "zsentinel-queue-pressure-v1"
SOURCE = "local_sqlite_transaction_query_only"
HEX64 = re.compile(r"^[a-f0-9]{64}$")
HEX16 = re.compile(r"^[a-f0-9]{16}$")
SLUG = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
MAX_GROUPS = 300
MAX_SAMPLES = 20
MAX_HOURS = 72
PRIORITIES = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
STATUSES = ("queued", "claimed", "running", "verifying")


def _time(raw):
    if type(raw) is not str or not 1 <= len(raw) <= 45:
        raise ValueError("queue source clock invalid")
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("queue source clock invalid") from exc
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("queue source must have UTC clock")
    return value.astimezone(timezone.utc)


def _non_negative(row, field, maximum=2500):
    value = row.get(field)
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError("invalid queue source count: " + field)
    return value


def _source(value):
    if (type(value) is not dict or value.get("schema") != QUEUE_SCHEMA
            or value.get("source") != SOURCE
            or value.get("database_read_only") is not True
            or value.get("queue_inventory_complete") is not True
            or value.get("claim_ownership_verified") is not False
            or value.get("queue_write_authorized") is not False
            or value.get("automatic_retry_authorized") is not False
            or value.get("attempts_are_claims_not_failures") is not True
            or type(value.get("sample_is_complete")) is not bool):
        raise ValueError("unverified or incomplete read-only queue snapshot")
    key = value.get("source_file_identity_sha256_private")
    if type(key) is not str or not HEX64.fullmatch(key):
        raise ValueError("queue snapshot is missing same-source identity")
    groups = value.get("project_groups")
    samples = value.get("high_churn_samples")
    totals = value.get("totals")
    if (type(groups) is not list or len(groups) > MAX_GROUPS
            or type(samples) is not list or len(samples) > MAX_SAMPLES
            or type(totals) is not dict):
        raise ValueError("bounded queue source required")
    active = _non_negative(totals, "active_items")
    queued = _non_negative(totals, "queued_items")
    owned = _non_negative(totals, "owned_items")
    high = _non_negative(totals, "high_claim_attempt_items")
    lease = _non_negative(totals, "expired_owner_lease_rows")
    unverifiable = _non_negative(totals, "unverifiable_owner_lease_rows")
    _non_negative(totals, "max_claim_attempts", 10**9)
    _non_negative(totals, "invalid_record_rows")
    if queued + owned != active or high > active or lease + unverifiable > owned:
        raise ValueError("incoherent queue source totals")
    table = {}
    for row in groups:
        if type(row) is not dict:
            raise ValueError("invalid queue project row")
        project, priority = row.get("project_id"), row.get("priority")
        if (type(project) is not str or not SLUG.fullmatch(project)
                or priority not in PRIORITIES or (project, priority) in table):
            raise ValueError("duplicate or invalid queue group")
        counts = {}
        for field in (*STATUSES, "high_claim_attempts", "ten_plus_claim_attempts",
                      "expired_owner_lease_rows", "unverifiable_owner_lease_rows"):
            counts[field] = _non_negative(row, field)
        counts["max_claim_attempts"] = _non_negative(row, "max_claim_attempts", 10**9)
        if (counts["high_claim_attempts"] > sum(counts[x] for x in STATUSES)
                or counts["ten_plus_claim_attempts"] > counts["high_claim_attempts"]):
            raise ValueError("queue group counters inconsistent")
        table[(project, priority)] = counts
    sample_map = {}
    for row in samples:
        if type(row) is not dict:
            raise ValueError("invalid claim attempt sample")
        ref, project, priority = row.get("queue_ref"), row.get("project_id"), row.get("priority")
        claim_count = row.get("claim_attempts")
        status = row.get("status")
        identity = (ref, project, priority)
        if (type(ref) is not str or not HEX16.fullmatch(ref)
                or type(project) is not str or not SLUG.fullmatch(project)
                or priority not in PRIORITIES or status not in STATUSES
                or (project, priority) not in table or identity in sample_map
                or type(claim_count) is not int or not 0 <= claim_count <= 10**9
                or type(row.get("owner_lease_expired_observed")) is not bool
                or type(row.get("owner_lease_unverifiable_observed")) is not bool
                or (row["owner_lease_expired_observed"] and
                    row["owner_lease_unverifiable_observed"])):
            raise ValueError("invalid queue claim identity or status")
        sample_map[identity] = {
            "attempts": claim_count,
            "status": status,
            "lease_expired": row["owner_lease_expired_observed"],
            "lease_unknown": row["owner_lease_unverifiable_observed"],
        }
    return key, _time(value.get("observed_at_utc")), table, sample_map, totals


def compare(previous, current, now=None):
    source0, time0, groups0, refs0, total0 = _source(previous)
    source1, time1, groups1, refs1, total1 = _source(current)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("comparison clock must be timezone-aware")
    now = now.astimezone(timezone.utc)
    if (source0 != source1
            or not timedelta(0) < time1 - time0 <= timedelta(hours=MAX_HOURS)
            or time1 > now + timedelta(minutes=2)
            or time0 > now + timedelta(minutes=2)):
        raise ValueError("snapshots must be chronologically ordered and from the exact same SQLite file")
    common_keys = refs0.keys() & refs1.keys()
    increases = []
    for identity in common_keys:
        before, after = refs0[identity], refs1[identity]
        if after["attempts"] > before["attempts"]:
            _, project, priority = identity
            increases.append({
                "project_id": project,
                "priority": priority,
                "new_claim_attempts_observed": after["attempts"] - before["attempts"],
                "previous_claim_attempts": before["attempts"],
                "current_claim_attempts": after["attempts"],
                "previous_status": before["status"],
                "current_status": after["status"],
                "owner_lease_expired_observed": after["lease_expired"],
                "owner_lease_unverifiable_observed": after["lease_unknown"],
                "lease_released_or_claimable_verified": False,
                "claim_failure_verified": False,
                # No queue ref, UUID, claim token or human-authored title.
            })
    increases.sort(key=lambda row: (
        PRIORITIES[row["priority"]], -row["new_claim_attempts_observed"],
        row["project_id"], row["current_status"]))
    group_changes = []
    for key in sorted(groups1.keys() & groups0.keys(),
                      key=lambda k: (PRIORITIES[k[1]], k[0])):
        before, after = groups0[key], groups1[key]
        change = after["high_claim_attempts"] - before["high_claim_attempts"]
        if change == 0 and before["queued"] == after["queued"]:
            continue
        group_changes.append({
            "project_id": key[0], "priority": key[1],
            "high_attempt_active_count_delta_not_retries": change,
            "queued_count_delta_not_retry_proof": after["queued"] - before["queued"],
            "current_expired_owner_lease_rows": after["expired_owner_lease_rows"],
            "current_unverifiable_owner_lease_rows": after["unverifiable_owner_lease_rows"],
            "owner_verified": False,
            "queue_write_authorized": False,
        })
    group_changes = group_changes[:40]
    count = len(increases)
    stage = ("observed_same_item_claim_attempt_increase" if count else
             "no_confirmed_repeat_within_bounded_sample")
    if count:
        action = ("Bevoegd queue-eigenaar onderzoekt de exacte lease- en worker-events van "
                  "waargenomen items. Geen automatische retry, claimrelease of failover.")
    elif (not previous["sample_is_complete"] or not current["sample_is_complete"]):
        action = ("De steekproef is afgekapt; selecteer zo nodig een nieuwe read-only "
                  "bronobservatie. Afwezigheid van retry churn is NIET bewezen.")
    else:
        action = ("Geen toegenomen pogingen binnen dezelfde getraceerde actieve items; "
                  "andere verdwenen of nieuwe items blijven onbekend.")
    return {
        "schema": SCHEMA,
        "previous_observed_at_utc": time0.isoformat(),
        "current_observed_at_utc": time1.isoformat(),
        "coverage": "matched_private_active_sample_only",
        "stage": stage,
        "action": action,
        "observed_same_item_attempt_increases": count,
        "observed_claim_attempt_increment_min": sum(
            x["new_claim_attempts_observed"] for x in increases),
        "matched_active_private_samples": len(common_keys),
        "previous_sample_complete": previous["sample_is_complete"],
        "current_sample_complete": current["sample_is_complete"],
        "source_active_item_delta_not_queue_growth_proof": (
            total1["active_items"] - total0["active_items"]),
        "source_high_attempt_item_delta_not_retry_proof": (
            total1["high_claim_attempt_items"] - total0["high_claim_attempt_items"]),
        "current_expired_lease_candidates_owner_review": total1["expired_owner_lease_rows"],
        "current_unverifiable_lease_candidates_owner_review": total1["unverifiable_owner_lease_rows"],
        "sampled_increased_claims": increases[:MAX_SAMPLES],
        "sampled_group_changes": group_changes,
        "sampled_changes_truncated": count > MAX_SAMPLES or
                                     len(groups0.keys() & groups1.keys()) > 40,
        "claims_taken": False,
        "claim_failure_proven": False,
        "queue_writes_performed": False,
        "automatic_retry_authorized": False,
        "lease_release_authorized": False,
        "worker_restart_authorized": False,
        "active_owners_displaced": False,
    }


def public_summary(report):
    if (type(report) is not dict or report.get("schema") != SCHEMA
            or any(report.get(key) is not False for key in (
                "claims_taken", "claim_failure_proven", "queue_writes_performed",
                "automatic_retry_authorized", "lease_release_authorized",
                "worker_restart_authorized", "active_owners_displaced"))
            or type(report.get("observed_same_item_attempt_increases")) is not int
            or type(report.get("observed_claim_attempt_increment_min")) is not int):
        raise ValueError("untrusted queue churn evidence")
    return {
        "schema": "zsentinel-queue-claim-trend-public-v1",
        "coverage": report["coverage"],
        "stage": report["stage"],
        "same_item_attempt_increases_observed": report["observed_same_item_attempt_increases"],
        "claim_attempt_increment_min_not_failures": report["observed_claim_attempt_increment_min"],
        "current_expired_lease_candidates_owner_review":
            report["current_expired_lease_candidates_owner_review"],
        "current_unverifiable_lease_candidates_owner_review":
            report["current_unverifiable_lease_candidates_owner_review"],
        "comparison_private_sample_only": True,
        "claim_failure_proven": False,
        "automatic_retry_authorized": False,
        "lease_release_authorized": False,
        "queue_identity_disclosed": False,
    }


def human_text(report):
    summary = public_summary(report)
    count = summary["same_item_attempt_increases_observed"]
    return ("zSentinel queue #677 | probleem: " +
            ("bij " + str(count) + " dezelfde actieve items steeg de claimpogingenteller."
             if count else "geen herhaalde claimpoging binnen de begrensde overlap bewezen.") +
            "\nACTIE: " + report["action"] +
            "\nSTATUS: attempts zijn claimpogingen, GEEN bewezen fouten; "
            "geen leasevrijgave/retry; brondekking " + summary["coverage"] + ".")


def public_html(report):
    r = public_summary(report)
    return ('<section aria-label="zSentinel queue claimpogingen">'
            '<h2>Queue · claimpogingen (#677)</h2>'
            '<p><strong>' +
            str(r["same_item_attempt_increases_observed"]) +
            '</strong> eerder herkende actieve items met extra claimpogingen. '
            'Totaal waargenomen toename: ' +
            str(r["claim_attempt_increment_min_not_failures"]) +
            ' pogingen (GEEN bewijs van mislukkingen).</p>'
            '<p>Verlopen lease-indicaties: ' +
            str(r["current_expired_lease_candidates_owner_review"]) +
            '; lease-klokken onzeker: ' +
            str(r["current_unverifiable_lease_candidates_owner_review"]) +
            '. Alleen eigenaarbeoordeling; geen automatische claimrelease of retry.</p>'
            '<p><a href="https://github.com/Zennay/zCloud/issues/677" '
            'rel="noopener noreferrer">Open issue #677</a></p></section>')


def private_html(report):
    public_summary(report)
    details = []
    for item in report["sampled_increased_claims"][:MAX_SAMPLES]:
        details.append(
            "<tr><td>" + escape(item["project_id"]) + "</td><td>" +
            escape(item["priority"]) + "</td><td>" +
            str(item["previous_claim_attempts"]) + " → " +
            str(item["current_claim_attempts"]) + "</td><td>" +
            escape(item["previous_status"]) + " → " +
            escape(item["current_status"]) + "</td></tr>")
    return (
        '<!doctype html><html lang="nl"><meta charset="utf-8">'
        '<meta name="referrer" content="no-referrer">'
        '<meta http-equiv="Content-Security-Policy" content="default-src &#39;none&#39;;'
        'style-src &#39;unsafe-inline&#39;;base-uri &#39;none&#39;">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>zSentinel · Queue-herstelbord</title>'
        '<style>body{font:16px system-ui;max-width:1000px;line-height:1.5;'
        'padding:1rem;margin:1rem auto;background:#0b1220;color:#f8fafc}'
        'a{color:#93c5fd}table{border-collapse:collapse;width:100%}'
        'td,th{padding:.5rem;border:1px solid #64748b}</style>'
        '<main><h1>zSentinel · Herhaalde claimpogingen</h1>' +
        public_html(report) +
        '<p>' + escape(report["action"]) + '</p>'
        '<table><thead><tr><th>Project</th><th>Prioriteit</th>'
        '<th>Claimpogingen</th><th>Status</th></tr></thead><tbody>' +
        "".join(details) +
        '</tbody></table><p>Alleen herkende overlappende samples; verdwenen/new '
        'items en niet gescande leases zijn onbekend.</p></main></html>')


def main():
    parser = argparse.ArgumentParser(description="Private queue claim-attempt source snapshots and operator report")
    parser.add_argument("--database", type=Path, required=True,
                        help="one existing read-only SQLite queue (never changed)")
    parser.add_argument("--previous", type=Path, help="earlier private 0600 JSON snapshot of same queue source")
    parser.add_argument("--snapshot-output", type=Path,
                        help="new create-only private 0600 JSON of current snapshot")
    parser.add_argument("--output", type=Path, help="new private trend .html/.json/.md report")
    parser.add_argument("--format", choices=("text", "html", "json", "md"), default="text")
    args = parser.parse_args()
    try:
        if not args.previous and not args.snapshot_output:
            raise ValueError("choose a previous snapshot or save a new snapshot")
        if args.previous and args.output is None and args.format != "text":
            raise ValueError("HTML/JSON/Markdown require an explicit new private output file")
        if args.output and not args.previous:
            raise ValueError("comparison report requires previous snapshot")
        if args.output and args.format == "text":
            raise ValueError("text output is stdout only")
        if args.previous and args.snapshot_output and args.previous.resolve() == args.snapshot_output.resolve():
            raise ValueError("previous and new snapshot paths overlap")
        if args.output and args.snapshot_output and args.output.resolve() == args.snapshot_output.resolve():
            raise ValueError("snapshot and trend outputs overlap")
        latest = observe(args.database)
        trend = compare(load_previous(args.previous), latest) if args.previous else None
        if trend and args.output:
            content = (private_html(trend) if args.format == "html"
                       else json.dumps(trend, ensure_ascii=True, sort_keys=True, indent=2)
                       if args.format == "json" else human_text(trend))
            fmt = "md" if args.format == "md" else args.format
            write_private_output(args.output, content, fmt)
        if args.snapshot_output:
            write_private_output(
                args.snapshot_output, json.dumps(latest, ensure_ascii=True,
                                                 sort_keys=True, indent=2), "json")
        if trend:
            print(human_text(trend) if args.format == "text" else
                  "Private read-only queue trend created; no source identity published.")
        else:
            print("Private queue snapshot created; compare it to a later read-only observation.")
    except (OSError, ValueError, json.JSONDecodeError):
        parser.exit(2, "zSentinel queue trend unavailable; no queue/claim changes made.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

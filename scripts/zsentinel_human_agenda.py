#!/usr/bin/env python3
"""Export zSentinel's verified human-only escalation candidates to a private iCalendar.

An ICS file is an *operator-initiated export*, not a sent notification or
calendar change. Never export ordinary CI waits, retryable tasks, or secrets.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone

ALERT_ID = re.compile(r"^[0-9a-f]{32}$")
PROJECT = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$")
PROBLEM = re.compile(r"^[a-z][a-z0-9_]{0,119}$")


def _timestamp(value):
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError("invalid human review deadline")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid human review deadline") from exc
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("human review deadline must have a timezone")
    return stamp.astimezone(timezone.utc)


def _plain(value, maximum):
    if (not isinstance(value, str) or not 1 <= len(value) <= maximum
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError("invalid human-only calendar text")
    return value


def _calendar_text(value):
    return (value.replace("\\", "\\\\").replace(";", "\\;")
            .replace(",", "\\,").replace("\n", "\\n"))


def _fold(line):
    # RFC 5545 physical lines: max 75 UTF-8 octets, continuation starts
    # with a single space. Avoid splitting multi-byte Unicode characters.
    result = []
    current = ""
    for ch in line:
        addition = len(ch.encode("utf-8"))
        if len(current.encode("utf-8")) + addition > 75:
            result.append(current)
            current = " " + ch
        else:
            current += ch
    result.append(current)
    return "\r\n".join(result)


def human_calendar(decision, *, include_historical=False):
    """Offline ICS with fresh human incidents and opt-in historical owner checks.

    Historical entries use the original review deadline, never an invented
    future alarm. Importing the file remains a deliberate operator action.
    """
    if (type(include_historical) is not bool or not isinstance(decision, dict)
            or type(decision.get("schema_version")) is not int
            or decision["schema_version"] != 1
            or decision.get("mutations_performed") is not False
            or decision.get("watch_delivery_performed") is not False
            or decision.get("deployment_authorized") is not False
            or decision.get("queue_claims_changed") is not False
            or decision.get("ci_retried") is not False):
        raise ValueError("cannot export an authorizing or untrusted decision")
    alerts = decision.get("watch_alert_candidates")
    incidents = decision.get("incidents")
    if (not isinstance(alerts, list) or len(alerts) > 5
            or not isinstance(incidents, list) or len(incidents) > 200):
        raise ValueError("unbounded human-only incident source")
    human = {}
    for row in incidents:
        if not isinstance(row, dict):
            raise ValueError("invalid source incident")
        if row.get("human_escalation") is True:
            ident = row.get("incident_id")
            if not isinstance(ident, str) or not ALERT_ID.fullmatch(ident) or ident in human:
                raise ValueError("noncanonical human incident identity")
            human[ident] = row
    now = datetime.now(timezone.utc)
    # The receipt's observation time, not the export clock, tells the owner
    # how historical each suggested action is.
    observed = _timestamp(decision.get("observed_at_utc"))
    if observed > now + timedelta(minutes=2):
        raise ValueError("human calendar source is in the future")
    observed_utc = observed.strftime("%Y%m%dT%H%M%SZ")
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0",
        "PRODID:-//zSentinel//Human Only Operator Agenda//NL",
        "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
        "X-WR-CALNAME:zSentinel - alleen menselijke blokkades",
        "X-ZSENTINEL-SOURCE-UTC:" + observed_utc,
        "X-ZSENTINEL-NO-AUTHORITY:TRUE",
    ]
    seen = set()
    for alert in alerts:
        if not isinstance(alert, dict) or alert.get("delivered") is not False:
            raise ValueError("untrusted watch delivery state")
        key = alert.get("alert_id")
        if not isinstance(key, str) or not ALERT_ID.fullmatch(key) or key in seen:
            raise ValueError("invalid or duplicate watch candidate")
        seen.add(key)
        origin = human.get(key)
        if origin is None or any(alert.get(left) != origin.get(right) for left, right in (
            ("project_id", "project_id"), ("severity", "severity"),
            ("problem", "problem"), ("action", "action"),
            ("status", "status"), ("review_by", "target_review_by"),
        )):
            raise ValueError("watch candidate not bound to canonical human incident")
        pid = _plain(alert["project_id"], 80)
        if not PROJECT.fullmatch(pid):
            raise ValueError("invalid human calendar project")
        severity = alert["severity"]
        if severity not in ("P0", "P1"):
            raise ValueError("human agenda only accepts P0/P1")
        problem = _plain(alert["problem"], 120)
        if not PROBLEM.fullmatch(problem):
            raise ValueError("invalid human escalation kind")
        action = _plain(alert["action"], 600)
        status = _plain(alert["status"], 80)
        review = _timestamp(alert["review_by"])
        # Preserve the source deadline, including overdue incidents. Never
        # pretend a new later due time or owner acknowledgement exists.
        uid = hashlib.sha256((pid + ":" + key).encode("utf-8")).hexdigest()[:32]
        summary = _calendar_text(severity + " zSentinel " + pid + " - " + problem)
        description = _calendar_text(
            "Actie eigenaar: " + action + "\nStatus: " + status +
            "\nBronmoment UTC: " + observed.isoformat() +
            "\nBron: onvolledig/actueel alleen voor het onderzochte venster." +
            "\nControleer opnieuw of eigenaar, issue en gates nog actueel zijn." +
            "\nDit is een handmatig geimporteerde herinnering. Geen live herstelbewijs.")
        lines += [
            "BEGIN:VEVENT", "UID:" + uid + "@zsentinel.local",
            "DTSTAMP:" + stamp,
            "DTSTART:" + review.strftime("%Y%m%dT%H%M%SZ"),
            "DTEND:" + (review + timedelta(minutes=15)).strftime("%Y%m%dT%H%M%SZ"),
            "SUMMARY:" + summary, "DESCRIPTION:" + description,
            "STATUS:TENTATIVE", "CLASS:PRIVATE", "TRANSP:TRANSPARENT",
            # The receiving calendar MAY show a private reminder, but no
            # notification is sent by zSentinel. Import is always voluntary.
            "BEGIN:VALARM", "ACTION:DISPLAY", "TRIGGER:-PT15M",
            "DESCRIPTION:" + _calendar_text("zSentinel menselijke beoordeling: " + pid),
            "END:VALARM", "END:VEVENT",
        ]
    if include_historical:
        rechecks = decision.get("historical_owner_recheck_candidates", [])
        historical = decision.get("historical_unresolved", {})
        shown = historical.get("shown") if isinstance(historical, dict) else None
        if (type(rechecks) is not list or len(rechecks) > 5
                or type(shown) is not list or len(shown) > 30
                or historical.get("action_authorized") is not False
                or historical.get("source_partial") is not True):
            raise ValueError("untrusted opt-in historical calendar source")
        ledger = {}
        for item in shown:
            if type(item) is not dict or type(item.get("incident_id")) is not str:
                raise ValueError("invalid historical calendar ledger row")
            ident = item["incident_id"]
            if ident in ledger:
                raise ValueError("duplicate historical calendar source")
            ledger[ident] = item
        for row in rechecks:
            if type(row) is not dict:
                raise ValueError("untrusted historical calendar candidate")
            ident = row.get("incident_id")
            origin = ledger.get(ident)
            if (type(ident) is not str or not ALERT_ID.fullmatch(ident)
                    or origin is None or ident in seen
                    or origin.get("human_escalation_historical") is not True
                    or origin.get("source_status") != "not_observed_needs_reconciliation"
                    or row.get("source_status") != "historical_not_observed_needs_owner_recheck"
                    or row.get("current_source_observed") is not False
                    or row.get("notifier_delivery_authorized") is not False
                    or row.get("recovery_action_authorized") is not False
                    or row.get("owner_confirmation_required") is not True
                    or row.get("project_id") != origin.get("project_id")
                    or row.get("severity_at_last_observation") != origin.get("severity")
                    or row.get("problem_at_last_observation") != origin.get("problem")
                    or row.get("source_last_seen") != origin.get("last_seen")
                    or row.get("exact_github_job") != origin.get("exact_github_job")):
                raise ValueError("historical calendar source does not match ledger")
            seen.add(ident)
            pid = _plain(row["project_id"], 80)
            problem = _plain(row["problem_at_last_observation"], 120)
            severity = row["severity_at_last_observation"]
            if not PROJECT.fullmatch(pid) or not PROBLEM.fullmatch(problem) or severity not in ("P0", "P1"):
                raise ValueError("invalid historical calendar label")
            first = _timestamp(origin["first_seen"])
            last = _timestamp(origin["last_seen"])
            if first > last or last > observed + timedelta(minutes=2):
                raise ValueError("historical calendar times contradict decision")
            # Preserve the original priority-based review target, even if old.
            original_due = first + timedelta(hours=2 if severity == "P0" else 12)
            uid = hashlib.sha256(("historical:" + pid + ":" + ident).encode("utf-8")).hexdigest()[:32]
            summary = _calendar_text("HISTORISCH " + severity + " zSentinel " +
                                     pid + " - " + problem)
            description = _calendar_text(
                "Historische bron: laatst gezien " + last.isoformat() +
                "\\nBevoegde eigenaar: verifieer huidige exacte job/issue en gates." +
                "\\nGEEN nieuw incident, geen actuele bron, geen autoherstel of melding." +
                "\\nDe oorspronkelijke reviewtermijn is behouden en kan verlopen zijn.")
            lines.extend([
                "BEGIN:VEVENT", "UID:" + uid + "@zsentinel.local",
                "DTSTAMP:" + stamp,
                "DTSTART:" + original_due.strftime("%Y%m%dT%H%M%SZ"),
                "DTEND:" + (original_due + timedelta(minutes=15)).strftime("%Y%m%dT%H%M%SZ"),
                "SUMMARY:" + summary, "DESCRIPTION:" + description,
                "X-ZSENTINEL-HISTORICAL-SOURCE:TRUE",
                "STATUS:TENTATIVE", "CLASS:PRIVATE", "TRANSP:TRANSPARENT",
                # NO VALARM on historical entries: nothing is sent or
                # scheduled as a fresh warning by this exporter.
                "END:VEVENT",
            ])
        if historical.get("truncated") is True or decision.get("historical_recheck_source_truncated") is True:
            lines.append("X-ZSENTINEL-HISTORICAL-SAMPLE:INCOMPLETE")
    if not alerts and not include_historical:
        lines.append("X-ZSENTINEL-COVERAGE:NO-HUMAN-ACTION-CANDIDATES-IN-BOUNDED-SAMPLE")
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"

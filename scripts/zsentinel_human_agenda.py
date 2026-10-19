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


def human_calendar(decision):
    """One bounded, script-free ICS containing only actionable human incidents."""
    if (not isinstance(decision, dict)
            or type(decision.get("schema_version")) is not int
            or decision["schema_version"] != 1
            or decision.get("mutations_performed") is not False
            or decision.get("watch_delivery_performed") is not False
            or decision.get("deployment_authorized") is not False
            or decision.get("queue_claims_changed") is not False):
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
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0",
        "PRODID:-//zSentinel//Human Only Operator Agenda//NL",
        "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
        "X-WR-CALNAME:zSentinel - alleen menselijke blokkades",
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
            "\nBron: onvolledig/actueel alleen voor het onderzochte venster." +
            "\nDit is een handmatig geimporteerde herinnering. Geen live herstelbewijs.")
        lines += [
            "BEGIN:VEVENT", "UID:" + uid + "@zsentinel.local",
            "DTSTAMP:" + stamp,
            "DTSTART:" + review.strftime("%Y%m%dT%H%M%SZ"),
            "DTEND:" + (review + timedelta(minutes=15)).strftime("%Y%m%dT%H%M%SZ"),
            "SUMMARY:" + summary, "DESCRIPTION:" + description,
            "STATUS:TENTATIVE", "CLASS:PRIVATE", "TRANSP:TRANSPARENT",
            "END:VEVENT",
        ]
    if not alerts:
        lines.append("X-ZSENTINEL-COVERAGE:NO-HUMAN-ACTION-CANDIDATES-IN-BOUNDED-SAMPLE")
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"

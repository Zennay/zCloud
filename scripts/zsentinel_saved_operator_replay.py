#!/usr/bin/env python3
"""Offline, owner-private replay of an existing zSentinel live cockpit snapshot.

Never recollect GitHub/Notion/SQLite, dispatch, merge or touch runner services.
Generated views are historical copies, not new observations or authorization.
"""
from __future__ import annotations

import argparse
import os
import stat
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

from zsentinel_recovery_router import read_json
from zsentinel_private_output import write_private_output

MAX_SOURCE_BYTES = 6 * 1024 * 1024
MAX_HISTORY = timedelta(days=30)
NO_AUTHORITY = (
    "mutations_performed", "queue_claims_changed", "ci_retried",
    "deployment_authorized", "watch_delivery_performed",
)
FORMATS = {"html": "html", "desk": "html", "ics": "ics",
           "md": "md", "text": "txt"}


def archived_decision(path: Path, now: datetime | None = None) -> tuple[dict, datetime]:
    """Admit only a private owner-readable, bounded historical decision."""
    if not isinstance(path, Path):
        raise ValueError("saved snapshot must be an explicit file")
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o077
            or info.st_nlink != 1 or not 0 < info.st_size <= MAX_SOURCE_BYTES):
        raise ValueError("saved source must be an owner-only 0600 regular file")
    decision = read_json(path, MAX_SOURCE_BYTES, "saved decision")
    if (type(decision) is not dict
            or type(decision.get("schema_version")) is not int
            or decision["schema_version"] != 1
            or any(decision.get(field) is not False for field in NO_AUTHORITY)):
        raise ValueError("saved snapshot is not a non-authorizing router decision")
    value = decision.get("observed_at_utc")
    if type(value) is not str or not 1 <= len(value) <= 40:
        raise ValueError("saved snapshot has no canonical source timestamp")
    try:
        observed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("saved snapshot has invalid source timestamp") from exc
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise ValueError("saved snapshot timestamp lacks UTC offset")
    observed = observed.astimezone(timezone.utc)
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if observed > now + timedelta(minutes=2) or now - observed > MAX_HISTORY:
        raise ValueError("saved snapshot is too old or from the future")
    # Admission to a known renderer is more restrictive than the top-level
    # schema check and rejects unknown/overlarge nested issue/queue records.
    from zsentinel_recovery_view import html_view
    html_view(decision)
    return decision, observed


def render_historical(decision: dict, observed: datetime, fmt: str) -> str:
    """Present the old source honestly; never stamp it as newly collected."""
    if fmt not in FORMATS:
        raise ValueError("unsupported historical operator format")
    stamp = observed.isoformat().replace("+00:00", "Z")
    warning = ("HISTORISCHE LOKALE BRON " + stamp
               + " — GEEN NIEUWE LIVE GITHUB/NOTION/QUEUE-LEZING; "
               + "CLAIMS, CI, MERGE EN HERSTEL ONBEKEND. Controleer opnieuw.")
    if fmt == "html":
        from zsentinel_recovery_view import html_view
        rendered = html_view(decision)
        if rendered.count("<main>") != 1:
            raise ValueError("incompatible historical HTML view")
        notice = ('<section class="panel warn"><strong>HISTORISCH ARCHIEF</strong>'
                  '<p>' + escape(warning) + '</p></section>')
        return rendered.replace("<main>", "<main>" + notice, 1)
    if fmt == "desk":
        from zsentinel_incident_desk import review_html
        rendered = review_html(decision)
        # The incident desk has a different HTML template from the ordinary
        # cockpit. Keep its immutable source in place, but mark saved replays
        # conspicuously as historical so users cannot mistake old owner
        # decisions for new GitHub/queue/CI evidence.
        marker = "<header><small>zCLOUD / zSENTINEL / READ-ONLY SOURCE</small>"
        if rendered.count(marker) != 1:
            raise ValueError("incompatible historical incident desk")
        banner = ('<header><p class="banner"><strong>HISTORISCH ARCHIEF</strong>'
                  '<br>' + escape(warning) + '</p>'
                  '<small>zCLOUD / zSENTINEL / READ-ONLY SOURCE</small>')
        return rendered.replace(marker, banner, 1)
    if fmt == "ics":
        from zsentinel_human_agenda import human_calendar
        return human_calendar(decision)
    if fmt == "md":
        from zsentinel_runtime_handoff import build_handoff, serialize
        payload = serialize(build_handoff(decision), ".md")
        return "> " + warning + "\n\n" + payload
    from zsentinel_recovery_router import operator_text
    return warning + "\n\n" + operator_text(decision)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Reopen an owner-private historical zSentinel snapshot OFFLINE")
    parser.add_argument("--snapshot", type=Path, required=True,
                        help="previously saved owner-private 0600 live JSON report")
    parser.add_argument("--format", choices=tuple(FORMATS), default="html")
    parser.add_argument("--output", type=Path, required=True,
                        help="new 0600 historical file; never overwrite an existing file")
    args = parser.parse_args(argv)
    try:
        expected = "." + FORMATS[args.format]
        if args.output.suffix != expected:
            raise ValueError("historical output extension must match --format")
        if args.snapshot.resolve() == args.output.resolve():
            raise ValueError("historical source cannot also be the output")
        decision, observed = archived_decision(args.snapshot)
        rendered = render_historical(decision, observed, args.format)
        write_private_output(args.output, rendered, FORMATS[args.format])
    except (OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
        parser.exit(2, "zSentinel offline archive unavailable: " + str(exc)[:160] + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

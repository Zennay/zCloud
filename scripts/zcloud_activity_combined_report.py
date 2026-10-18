"""Private offline view of *both* zCloud local events and GitHub receipts.

This is an operator-driven presentation layer, not a live ingest, worker,
scheduler or trusted release/merge authority. Local event titles/details never
leave their input file; they are not included in the rendered HTML.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import stat
import sys

from scripts.zcloud_activity_ledger import _read
from scripts.zcloud_activity_operator_report import h, render_html, save_private
from scripts.zcloud_github_activity import ActivitySourceError, strict_json

MAX_LOCAL_BYTES = 1024 * 1024
MAX_LOCAL_ROWS = 80


def read_local_snapshot(path, projects):
    """Accept one bounded /api/activity JSON response, never private detail."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except (OSError, ValueError):
        raise ActivitySourceError("local_snapshot_unavailable") from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_LOCAL_BYTES:
            raise ActivitySourceError("local_snapshot_not_regular_or_too_large")
        with os.fdopen(os.dup(fd), "rb") as stream:
            raw = stream.read(MAX_LOCAL_BYTES + 1)
        if len(raw) > MAX_LOCAL_BYTES:
            raise ActivitySourceError("local_snapshot_too_large")
    finally:
        os.close(fd)
    try:
        data = strict_json(raw.decode("utf-8"))
    except (UnicodeError, ValueError, TypeError):
        raise ActivitySourceError("local_snapshot_invalid_json") from None
    if type(data) is not list or len(data) > MAX_LOCAL_ROWS:
        raise ActivitySourceError("local_snapshot_invalid_shape")
    rows = []
    seen = set()
    for value in data:
        if type(value) is not dict:
            raise ActivitySourceError("local_snapshot_invalid_event")
        project, identifier, when = (value.get(k) for k in ("project", "id", "ts"))
        if (type(project) is not str or project not in projects or
                type(identifier) is not str or not 1 <= len(identifier) <= 256 or
                type(when) is not str or len(when) > 42):
            raise ActivitySourceError("local_snapshot_untrusted_event")
        try:
            stamp = datetime.fromisoformat(when.replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                raise ValueError("timezone missing")
            utc = stamp.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except (ValueError, OverflowError):
            raise ActivitySourceError("local_snapshot_invalid_time") from None
        key = (project, identifier)
        if key in seen:
            continue
        seen.add(key)
        # Do not render or persist raw ID/title/detail. A digest is used only
        # as a deterministic secondary sort key and never leaves this function.
        rows.append((project, utc, hashlib.sha256(identifier.encode("utf-8")).digest()))
    return sorted(rows, key=lambda row: (row[1], row[0], row[2]), reverse=True)


def combined_html(ledger, rows, project=None):
    """Keep two sources visibly separate; never count GitHub and local twice."""
    html = render_html(ledger, project=project)
    visible = [row for row in rows if project is None or row[0] == project]
    count = {pid: 0 for pid in (sorted(ledger["coverage"]) if project is None else [project])}
    for pid, _, _ in visible:
        count[pid] += 1
    cards = "".join(
        "<div class='card'><strong>" + h(amount) + "</strong><small>" +
        h(pid) + " lokale events in deze export</small></div>"
        for pid, amount in count.items()
    )
    body = "".join(
        "<tr><td>" + h(pid) + "</td><td><time datetime='" + h(when) +
        "'>" + h(when) + "</time></td><td>Lokaal geregistreerd; niet geverifieerd als merge of CI</td></tr>"
        for pid, when, _ in visible
    )
    section = (
        "<section aria-labelledby='local-source-title'><h2 id='local-source-title'>"
        "Lokale VPS-activiteit · eenmalige export</h2>"
        "<p>Afzonderlijke /api/activity-momentopname. Iedere eventbron is maximaal "
        "80 records diep; ontbrekende activiteit is geen nulmeting. "
        "Deze export bewijst geen actuele VPS-verbinding.</p>"
        "<div class='grid'>" + cards + "</div><div class='wrap'><table>"
        "<thead><tr><th scope='col'>Project</th><th scope='col'>Bron-tijd</th>"
        "<th scope='col'>Betekenis</th></tr></thead><tbody>" +
        (body or "<tr><td colspan='3'>Geen lokale regels in deze export; "
         "andere activiteit is mogelijk.</td></tr>") +
        "</tbody></table></div></section>"
    )
    pivot = "</nav></header>"
    if html.count(pivot) != 1:
        raise ActivitySourceError("activity_report_markup_unexpected")
    html = html.replace(pivot, pivot + section, 1)
    html = html.replace("Dit overzicht omvat geen lokale VPS-events.",
                        "Lokale VPS-events worden hieronder als afzonderlijke export getoond.", 1)
    html = html.replace("<title>zCloud · Privé GitHub-activiteit</title>",
                        "<title>zCloud · Privé gecombineerde activiteit</title>", 1)
    html = html.replace("<h1>Projectactiviteit · GitHub</h1>",
                        "<h1>Projectactiviteit · VPS en GitHub</h1>", 1)
    html = html.replace("Bron: private zCloud GitHub-ledger.",
                        "Bronnen: private GitHub-ledger en afzonderlijke lokale VPS-export.", 1)
    return html


def main(argv=None):
    parser = argparse.ArgumentParser(description="Combine private retained GitHub activity and one local VPS event export into offline HTML")
    parser.add_argument("--ledger", required=True, help="Existing private GitHub source ledger (0600)")
    parser.add_argument("--local-json", required=True, help="Saved raw GET /api/activity response; never printed")
    parser.add_argument("--output", required=True, help="Private local HTML outside public/ (0600)")
    parser.add_argument("--project", help="Optional project from the private GitHub ledger")
    args = parser.parse_args(argv)
    ledger = _read(Path(args.ledger))
    if args.project is not None and args.project not in ledger["coverage"]:
        raise ActivitySourceError("activity_project_not_allowlisted")
    rows = read_local_snapshot(args.local_json, set(ledger["coverage"]))
    save_private(args.output, combined_html(ledger, rows, project=args.project))
    print("Private combined Activity report saved; both sources remain historical and not live verified.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ActivitySourceError as exc:
        sys.stderr.write("Private combined Activity report unavailable: " + str(exc) + "\n")
        raise SystemExit(2)

#!/usr/bin/env python3
"""Private, read-only fair Activity export for zCloud issue #1518.

This is an independent operator tool, NOT a production /api/activity endpoint.
It never reads event titles/details or claims GitHub PR/CI synchronization.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import html
import json
import os
from pathlib import Path
import sqlite3
import sys
from urllib.parse import quote

from zcloud_activity_fair_page import MAX_PAGE, MAX_PROJECTS, fair_page

MAX_CURSOR_BYTES = 16384
MAX_OUTPUT_BYTES = 2 * 1024 * 1024


def _load_cursor(path: str | None, selected: list[str]) -> dict | None:
    if not path:
        return None
    raw = Path(path).read_bytes()
    if len(raw) > MAX_CURSOR_BYTES:
        raise ValueError("cursor source too large")
    doc = json.loads(raw.decode("utf-8"))
    if not isinstance(doc, dict):
        raise ValueError("cursor document must be an object")
    # Accept only exports from this tool.  A cursor for another project set
    # must never silently skip rows in the selected projects.
    if doc.get("format_version") != 1 or doc.get("projects") != selected:
        raise ValueError("cursor export version or project scope mismatch")
    if not isinstance(doc.get("coverage"), dict) or doc["coverage"].get("local_sqlite") != "read_only":
        raise ValueError("cursor source provenance mismatch")
    cursor = doc.get("next_cursor")
    if cursor is None:
        raise ValueError("previous export has no next page")
    if not isinstance(cursor, dict):
        raise ValueError("invalid cursor in previous export")
    return cursor


def _read_page(db_path: str, projects: list[str], size: int, cursor: dict | None) -> dict:
    path = Path(db_path).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError("database path is not a file")
    # The mode=ro URI and query_only protect the runtime DB.  No migration,
    # table creation, lock acquisition for writing, or replay is attempted.
    uri = "file:" + quote(str(path), safe="/") + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=2)) as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA busy_timeout=2000")
        connection.execute("BEGIN")
        page = fair_page(connection, projects, page_size=size, cursor=cursor)
        connection.rollback()
    return page


def _document(page: dict, projects: list[str]) -> dict:
    return {
        "format_version": 1,
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "projects": projects,
        "events": page["events"],
        "next_cursor": page["next_cursor"],
        "coverage": page["coverage"],
        "interpretation": {
            "source": "LOCAL_SQLITE_ONLY",
            "github_pr_branch_ci": "NOT_SYNCHRONIZED",
            "main_merged": "NOT_VERIFIED",
            "live_deployed": "NOT_VERIFIED",
            "ownership_and_claims": "NOT_OBSERVED",
            "freshness": "LOCAL_READ_TIME_NOT_SOURCE_SYNC",
        },
    }


def _html(doc: dict) -> str:
    escape = lambda value: html.escape(str(value), quote=True)
    rows = []
    for item in doc["events"]:
        rows.append(
            "<tr><td>" + escape(item["ts"]) + "</td><td>"
            + escape(item["project"]) + "</td><td>"
            + escape(item["kind"]) + "</td><td><code>"
            + escape(item["id"]) + "</code></td></tr>"
        )
    if not rows:
        rows.append("<tr><td colspan='4'>Geen lokale gebeurtenissen waargenomen. "
                    "GitHub-dekking onbekend; dit bewijst geen inactiviteit.</td></tr>")
    if doc["next_cursor"] is None:
        pagination = "Geen volgende pagina binnen de gelezen lokale bron."
    else:
        pagination = ("Er is een volgende pagina. Bewaar voor vervolg de JSON-uitvoer "
                      "met dezelfde projectselectie en gebruik --cursor-file.")
    return """<!doctype html><html lang="nl"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>zCloud · lokale Activity</title>
<style>
:root{color-scheme:light dark}body{font:16px system-ui,sans-serif;
max-width:1050px;margin:auto;padding:20px;line-height:1.45}
table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid #8886;
padding:9px;text-align:left;vertical-align:top;overflow-wrap:anywhere}
table{display:block;overflow-x:auto}code{font-size:.84em}
small{display:block;margin:8px 0}p{max-width:75ch}
</style><main><h1>Lokale Activity per project</h1>
<p><strong>Gedeeltelijke bron:</strong> alleen de bestaande lokale SQLite-events.
Geen synchronisatie van GitHub-PR's, branches of CI. Geen bewijs van main-merge,
live deploy, actieve eigenaar of runnerherstel. Een groene serverstatus bewijst
geen complete Activity-feed.</p><p>Gelezen op: """ + escape(doc["observed_at_utc"]) + """</p>
<p>Geselecteerde projecten: """ + escape(", ".join(doc["projects"])) + """</p>
<table><thead><tr><th>Tijd (UTC)</th><th>Project</th><th>Soort</th><th>Event-ID</th></tr>
</thead><tbody>""" + "".join(rows) + """</tbody></table>
<p>""" + escape(pagination) + """</p>
<small>Alleen metadata; privé-eventtitels en -details worden niet gelezen.</small>
</main></html>"""


def _write_private(path: str, payload: bytes) -> None:
    if len(payload) > MAX_OUTPUT_BYTES:
        raise ValueError("export exceeds size limit")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as output:
            fd = -1
            output.write(payload)
    finally:
        if fd >= 0:
            os.close(fd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="existing local events SQLite database")
    parser.add_argument("--project", action="append", required=True,
                        help="explicit canonical project slug; repeat for fair comparison")
    parser.add_argument("--page-size", type=int, default=MAX_PAGE)
    parser.add_argument("--cursor-file", help="previous JSON export to continue pagination")
    parser.add_argument("--format", choices=("json", "html"), default="html")
    parser.add_argument("--output", help="create-only 0600 private output path; never overwrite")
    args = parser.parse_args(argv)
    try:
        projects = args.project
        if len(projects) > MAX_PROJECTS:
            raise ValueError("too many projects")
        cursor = _load_cursor(args.cursor_file, projects)
        page = _read_page(args.db, projects, args.page_size, cursor)
        doc = _document(page, projects)
        data = (json.dumps(doc, ensure_ascii=False, indent=2)
                if args.format == "json" else _html(doc)).encode("utf-8")
        if len(data) > MAX_OUTPUT_BYTES:
            raise ValueError("export exceeds size limit")
        if args.output:
            _write_private(args.output, data)
        else:
            sys.stdout.buffer.write(data + b"\n")
        return 0
    except (ValueError, OSError, sqlite3.Error, UnicodeError, TypeError,
            KeyError, json.JSONDecodeError) as exc:
        print("Activity export niet beschikbaar: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

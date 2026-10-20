#!/usr/bin/env python3
"""Read-only, source-limited four-lane zSentinel operator desk.

This utility NEVER claims work, changes leases, invokes runners or performs
production actions. Queue occupancy and ownership are deliberately distinct.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import html
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys

from lane_generator import generate_execution_lanes

LANE_NAMES = {
    "release-integration": "PRs naar main",
    "product-remediation": "Productproblemen",
    "worker-recovery": "Workers en wachtrij",
    "ci-deploy-recovery": "CI en deployments",
}
ACTIVE = {"claimed", "running", "verifying"}
VALID = ACTIVE | {"queued"}
MAX_ROWS = 500


def _utc(value):
    if not isinstance(value, str):
        return None
    try:
        date = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        return date.astimezone(timezone.utc) if date.tzinfo else None
    except (ValueError, OverflowError):
        return None


def collect(database, maximum):
    """Capture one bounded, read-only SQLite snapshot without owner identities."""
    path = Path(database).absolute()
    if not path.is_file():
        raise ValueError("SQLite source unavailable")
    uri = path.as_uri() + "?mode=ro"
    db = sqlite3.connect(uri, uri=True, timeout=2)
    try:
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA busy_timeout=2000")
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")
        queue_rows = db.execute(
            """SELECT queue_id, project_id, title, completion_criteria, priority,
                      status, created_at, metadata_json
                 FROM portfolio_queue WHERE project_id=?
                   AND lower(status) IN ('queued','claimed','running','verifying')
                 ORDER BY created_at, queue_id LIMIT ?""",
            ("zsentinel", maximum + 1),
        ).fetchall()
        claim_rows = db.execute(
            """SELECT project_id, lease_until, metadata_json
                 FROM task_claims WHERE project_id=? LIMIT ?""",
            ("zsentinel", maximum + 1),
        ).fetchall()
        db.execute("ROLLBACK")
    finally:
        db.close()

    queue_partial = len(queue_rows) > maximum
    claims_partial = len(claim_rows) > maximum
    queue = [dict(row) for row in queue_rows[:maximum]]
    now = datetime.now(timezone.utc)
    claims = []
    expired = 0
    ambiguous = 0
    for row in claim_rows[:maximum]:
        until = _utc(row["lease_until"])
        if until is None:
            ambiguous += 1
            continue
        if until <= now:
            expired += 1
            continue
        raw = row["metadata_json"]
        try:
            metadata = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            ambiguous += 1
            continue
        if not isinstance(metadata, dict):
            ambiguous += 1
            continue
        # Internal scope only; no owner_id/claim_key/worker_id ever loaded.
        claims.append({"project_id": "zsentinel", "metadata": metadata})
        if not isinstance(metadata.get("conflict_scope"), dict):
            ambiguous += 1

    lanes = generate_execution_lanes(
        {"id": "zsentinel", "lane_profile": "portfolio-recovery"}, queue, claims
    )
    observed = {status: sum(
        str(row.get("status", "")).lower() == status for row in queue
    ) for status in sorted(VALID)}
    visible = []
    for lane in lanes:
        conflicts = lane.get("blocked_by") or []
        visible.append({
            "id": lane["lane_id"],
            "name": LANE_NAMES[lane["lane_id"]],
            "status": lane["status"],
            "candidate_observed": lane.get("queue_id") is not None,
            "active_backlog_count": lane.get("active_backlog_count", 0),
            "parallel_active_warning": lane.get("parallel_active_warning", False),
            "conflicting_claims": sum(
                item.get("kind") == "claim" for item in conflicts
            ),
            "conflicting_queue_entries": sum(
                item.get("kind") == "queue" for item in conflicts
            ),
            # Scope, queue IDs and worker/claim identity are intentionally absent.
        })
    return {
        "schema": "zsentinel.four-lane-desk.v1",
        "observed_utc": now.replace(microsecond=0).isoformat(),
        "profile": "portfolio-recovery",
        "observed_queue": observed,
        "queue_partial": queue_partial,
        "active_local_leases_observed": len(claims),
        "expired_local_leases_observed": expired,
        "ambiguous_local_claims": ambiguous,
        "claims_partial": claims_partial,
        "claimed_without_local_lease_observation": (
            sum(observed[s] for s in ACTIVE) > 0 and len(claims) == 0
        ),
        "owner_free": "NOT_INFERABLE",
        "external_notion_claims": "NOT_OBSERVED",
        "merge_authorized": False,
        "dispatch_authorized": False,
        "release_authorized": False,
        "lanes": visible,
        "source_complete": not (queue_partial or claims_partial or ambiguous),
    }


def text_view(data):
    out = [
        "zSentinel · herstelbalie (alleen lezen)",
        "Bron: lokale, begrensde SQLite-snapshot; eigenaarvrijheid NIET afleidbaar.",
    ]
    for lane in data["lanes"]:
        extra = " | MEERDERE ACTIEVE TAKEN" if lane["parallel_active_warning"] else ""
        out.append(
            f"{lane['name']}: {lane['status']}; actieve taken "
            f"{lane['active_backlog_count']}; conflicten "
            f"{lane['conflicting_claims'] + lane['conflicting_queue_entries']}{extra}"
        )
    out.append(
        "Lokale claims: " + str(data["active_local_leases_observed"])
        + "; onduidelijk " + str(data["ambiguous_local_claims"])
    )
    if data["claimed_without_local_lease_observation"]:
        out.append("LET OP: claimed wachtrij-items zonder waargenomen lokale lease.")
    if not data["source_complete"]:
        out.append("LET OP: bron deels onvolledig; geen automatische herverdeling.")
    out.append("Geen claim-, merge-, runner- of deployautorisatie.")
    return "\n".join(out) + "\n"


def html_view(data):
    cards = []
    for lane in data["lanes"]:
        status = html.escape(str(lane["status"]))
        warning = (
            "<p class='warn'>Meerdere actieve taken; controleer eigenaars.</p>"
            if lane["parallel_active_warning"] else ""
        )
        count = lane["conflicting_claims"] + lane["conflicting_queue_entries"]
        cards.append(
            "<article><div class='eyebrow'>Herstelbaan</div><h2>"
            + html.escape(lane["name"]) + "</h2><p class='state'>"
            + status + "</p><dl><div><dt>Actief waargenomen</dt><dd>"
            + str(lane["active_backlog_count"])
            + "</dd></div><div><dt>Scopeconflicten</dt><dd>"
            + str(count) + "</dd></div></dl>" + warning + "</article>"
        )
    warnings = []
    if data["claimed_without_local_lease_observation"]:
        warnings.append(
            "Claimed taken zonder zichtbare lokale lease: niet herclaimen."
        )
    if not data["source_complete"]:
        warnings.append(
            "Gedeeltelijke of ambigue lokale bron: geen automatische herverdeling."
        )
    warnings.append(
        "Externe Notion-claims en releasegates zijn niet waargenomen; "
        "dit overzicht verleent geen schrijfrechten."
    )
    details = "".join("<li>" + html.escape(item) + "</li>" for item in warnings)
    return (
        "<!doctype html><html lang='nl'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<meta http-equiv='Content-Security-Policy' "
        "content=\"default-src 'none'; style-src 'unsafe-inline'; "
        "base-uri 'none'; form-action 'none'\">"
        "<title>zSentinel · herstelbalie</title><style>"
        ":root{color-scheme:dark}body{background:#0b1020;color:#eef2ff;"
        "font:16px system-ui,sans-serif;margin:0;padding:clamp(20px,4vw,50px)}"
        "main{max-width:1120px;margin:auto}header{margin-bottom:32px}"
        "h1{font-size:clamp(2rem,5vw,3.4rem);margin:8px 0}p{line-height:1.5}"
        ".eyebrow{font-size:.72rem;letter-spacing:.16em;text-transform:uppercase;"
        "color:#93a7ce}.grid{display:grid;grid-template-columns:"
        "repeat(auto-fit,minmax(240px,1fr));gap:16px}"
        "article,.notice{border:1px solid #30415c;border-radius:18px;"
        "padding:22px;background:#172136}article h2{font-size:1.15rem}"
        ".state{font-size:1.4rem;font-weight:700;color:#b9d0ff}"
        "dl{display:grid;grid-template-columns:1fr 1fr;gap:12px}"
        "dt{color:#9eb2d3;font-size:.82rem}dd{font-size:1.8rem;"
        "margin:4px 0;font-weight:700}.warn{color:#ffca83}"
        ".notice{margin-top:22px;background:#151d2e}"
        "li{margin:8px 0}footer{color:#a8b7d4;margin-top:28px}"
        "</style></head><body><main><header><div class='eyebrow'>"
        "Portfolio recovery · operatorweergave</div><h1>Vier herstelbanen</h1>"
        "<p>Actueel lokaal bronmoment: "
        + html.escape(data["observed_utc"]) + "</p></header><div class='grid'>"
        + "".join(cards) + "</div><section class='notice'><h2>Grenzen en "
        "vervolgactie</h2><ul>" + details + "</ul></section>"
        "<footer>Alleen lokale read-only bron. Geen claim, CI-start, merge, "
        "runneractie of deployment uitgevoerd.</footer></main></body></html>"
    )


def save_private(path, markup):
    dest = Path(path)
    if dest.suffix.lower() != ".html" or dest.name in ("", ".", ".."):
        raise ValueError("New private .html target required")
    directory = os.open(
        str(dest.parent), os.O_RDONLY | os.O_DIRECTORY
        | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        info = os.fstat(directory)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise ValueError("Private owner directory required")
        fd = os.open(
            dest.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=directory,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as target:
                target.write(markup)
                target.flush()
                os.fsync(target.fileno())
        except BaseException:
            os.unlink(dest.name, dir_fd=directory)
            raise
    finally:
        os.close(directory)


def main():
    parser = argparse.ArgumentParser(
        description="Read-only four-lane zSentinel occupancy desk"
    )
    parser.add_argument("--db", default="history.db", help="Read-only SQLite source")
    parser.add_argument("--max-rows", type=int, default=250)
    parser.add_argument("--format", choices=("text", "json", "html"), default="text")
    parser.add_argument("--output", help="New owner-private .html file, HTML only")
    args = parser.parse_args()
    if not 1 <= args.max_rows <= MAX_ROWS:
        parser.error("--max-rows must be between 1 and 500")
    if (args.format == "html") != bool(args.output):
        parser.error("HTML requires --output, other formats cannot write files")
    try:
        packet = collect(args.db, args.max_rows)
        if args.format == "html":
            save_private(args.output, html_view(packet))
            print("Privé herstelbalie gemaakt (0600); geen systeemactie.")
        elif args.format == "json":
            print(json.dumps(packet, ensure_ascii=False, sort_keys=True))
        else:
            print(text_view(packet), end="")
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
        parser.exit(1, "Geen betrouwbaar lokaal bronoverzicht; geen actie.\n")


if __name__ == "__main__":
    main()

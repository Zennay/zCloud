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

from lane_generator import (
    classify_backlog_item,
    generate_execution_lanes,
    scopes_overlap,
)

LANE_NAMES = {
    "release-integration": "PRs naar main",
    "product-remediation": "Productproblemen",
    "worker-recovery": "Workers en wachtrij",
    "ci-deploy-recovery": "CI en deployments",
}
ACTIVE = {"claimed", "running", "verifying"}
VALID = ACTIVE | {"queued"}
MAX_ROWS = 500
# Canonical real projects only; zSentinel is a recovery role, not a 14th.
PORTFOLIO_PROJECTS = {
    "cloud": "zCloud", "zssh": "zSSH", "ftmo": "FTMO",
    "haxlab": "HaxLab", "supa": "Supa", "raiseai": "RaiseAI",
    "ulab": "uLab", "lightup": "LightUp", "zguard": "zGuard",
    "tradengine": "TradeEngine", "mixlab": "MixLab",
    "fuckball": "Fuckball", "tuddy": "Tuddy",
}
PORTFOLIO_GROUP_LIMIT = 128


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
                      status, created_at, claimed_at, claim_expires, metadata_json
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
        priority_rows = db.execute(
            """SELECT project_id, UPPER(priority) AS priority,
                      LOWER(status) AS status, COUNT(*) AS amount
                 FROM portfolio_queue
                WHERE UPPER(priority) IN ('P0','P1')
                  AND LOWER(status) IN ('queued','claimed','running','verifying')
                GROUP BY project_id, UPPER(priority), LOWER(status)
                ORDER BY priority, project_id, status LIMIT ?""",
            (PORTFOLIO_GROUP_LIMIT + 1,),
        ).fetchall()
        db.execute("ROLLBACK")
    finally:
        db.close()

    queue_partial = len(queue_rows) > maximum
    claims_partial = len(claim_rows) > maximum
    portfolio_partial = len(priority_rows) > PORTFOLIO_GROUP_LIMIT
    unknown_project_groups = 0
    portfolio_counts = {}
    for row in priority_rows[:PORTFOLIO_GROUP_LIMIT]:
        project_id = row["project_id"]
        if project_id not in PORTFOLIO_PROJECTS:
            unknown_project_groups += 1
            continue
        values = portfolio_counts.setdefault(project_id, {
            "project": PORTFOLIO_PROJECTS[project_id],
            "queued_p0": 0, "queued_p1": 0, "in_flight_p0": 0,
            "in_flight_p1": 0,
        })
        key = ("queued_" if row["status"] == "queued" else "in_flight_") + row["priority"].lower()
        values[key] += int(row["amount"])
    portfolio = sorted(
        (value for value in portfolio_counts.values() if any(
            value[key] for key in ("queued_p0", "queued_p1", "in_flight_p0", "in_flight_p1")
        )),
        key=lambda v: (-v["queued_p0"], -v["queued_p1"], v["project"]),
    )
    queue = [dict(row) for row in queue_rows[:maximum]]
    now = datetime.now(timezone.utc)
    claims = []
    expired = 0
    ambiguous = 0
    expired_queue_claims = 0
    unknown_queue_claim_expiry = 0
    for item in queue:
        if str(item.get("status", "")).lower() not in ACTIVE:
            continue
        expiry = _utc(item.get("claim_expires"))
        if expiry is None:
            unknown_queue_claim_expiry += 1
        elif expiry <= now:
            expired_queue_claims += 1
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

    project = {"id": "zsentinel", "lane_profile": "portfolio-recovery"}
    lanes = generate_execution_lanes(project, queue, claims)
    # Diagnostic only. The dispatch path itself remains governed by the
    # existing lane generator and actual external claim/release gates.
    active_items = [
        classify_backlog_item(project, item)
        for item in queue if str(item.get("status", "")).lower() in ACTIVE
    ]
    collision_counts = {lane_id: 0 for lane_id in LANE_NAMES}
    cross_lane_coverage = len(active_items) <= 64 and not queue_partial
    if cross_lane_coverage:
        for index, left in enumerate(active_items):
            for right in active_items[index + 1:]:
                if left["lane_id"] == right["lane_id"]:
                    continue
                overlap = scopes_overlap(left["scope"], right["scope"])
                if overlap["capabilities"] or overlap["files"]:
                    collision_counts[left["lane_id"]] += 1
                    collision_counts[right["lane_id"]] += 1
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
            "active_cross_lane_scope_conflicts": (
                collision_counts[lane["lane_id"]] if cross_lane_coverage else None
            ),
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
        "portfolio_urgent_local": portfolio,
        "portfolio_priority_partial": portfolio_partial,
        "portfolio_unknown_project_groups": unknown_project_groups,
        "queue_partial": queue_partial,
        "active_local_leases_observed": len(claims),
        "expired_local_leases_observed": expired,
        "ambiguous_local_claims": ambiguous,
        "claims_partial": claims_partial,
        "expired_in_flight_queue_claims_observed": expired_queue_claims,
        "unknown_in_flight_queue_expiry_observed": unknown_queue_claim_expiry,
        "claimed_without_local_lease_observation": (
            sum(observed[s] for s in ACTIVE) > 0 and len(claims) == 0
        ),
        "active_cross_lane_coverage_complete": cross_lane_coverage,
        "owner_free": "NOT_INFERABLE",
        "external_notion_claims": "NOT_OBSERVED",
        "merge_authorized": False,
        "dispatch_authorized": False,
        "release_authorized": False,
        "lanes": visible,
        "source_complete": not (
            queue_partial or claims_partial or ambiguous
            or portfolio_partial or unknown_project_groups
        ),
        "ownership_reconciliation_required": bool(
            expired_queue_claims or unknown_queue_claim_expiry
            or ambiguous or queue_partial or claims_partial
            or portfolio_partial or unknown_project_groups
            or not cross_lane_coverage
            or any(collision_counts.values())
            or (sum(observed[s] for s in ACTIVE) > 0 and len(claims) == 0)
        ),
    }


def text_view(data):
    out = [
        "zSentinel · herstelbalie (alleen lezen)",
        "Bron: lokale, begrensde SQLite-snapshot; eigenaarvrijheid NIET afleidbaar.",
    ]
    for lane in data["lanes"]:
        extra = " | MEERDERE ACTIEVE TAKEN" if lane["parallel_active_warning"] else ""
        if lane["active_cross_lane_scope_conflicts"]:
            extra += " | CONFLICT MET ANDERE ACTIEVE BAAN"
        elif lane["active_cross_lane_scope_conflicts"] is None:
            extra += " | ACTIEVE SCOPEDEKKING ONVOLLEDIG"
        out.append(
            f"{lane['name']}: {lane['status']}; actieve taken "
            f"{lane['active_backlog_count']}; conflicten "
            f"{lane['conflicting_claims'] + lane['conflicting_queue_entries']}{extra}"
        )
    out.append("Open lokale P0/P1-queue per echt project (adviserend):")
    for item in data["portfolio_urgent_local"]:
        out.append(
            f"  {item['project']}: P0 wacht {item['queued_p0']}; "
            f"P1 wacht {item['queued_p1']}; "
            f"P0 in behandeling {item['in_flight_p0']}; "
            f"P1 in behandeling {item['in_flight_p1']}"
        )
    if data["portfolio_priority_partial"] or data["portfolio_unknown_project_groups"]:
        out.append("  LET OP: portfolio-dekking gedeeltelijk of onbekende project-ID.")
    out.append(
        "Lokale claims: " + str(data["active_local_leases_observed"])
        + "; onduidelijk " + str(data["ambiguous_local_claims"])
    )
    if data["claimed_without_local_lease_observation"]:
        out.append("LET OP: claimed wachtrij-items zonder waargenomen lokale lease.")
    if data["expired_in_flight_queue_claims_observed"]:
        out.append("VERLOPEN queue-claimtermijnen: "
                   + str(data["expired_in_flight_queue_claims_observed"])
                   + " (niet automatisch herclaimen).")
    if data["unknown_in_flight_queue_expiry_observed"]:
        out.append("Onbekende queue-claimtermijnen: "
                   + str(data["unknown_in_flight_queue_expiry_observed"])
                   + " (controleer eigenaar).")
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
        if lane["active_cross_lane_scope_conflicts"]:
            warning += "<p class='warn'>Actief scopeconflict met andere herstelbaan.</p>"
        elif lane["active_cross_lane_scope_conflicts"] is None:
            warning += "<p class='warn'>Actieve scopevergelijking onvolledig.</p>"
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
    if data["expired_in_flight_queue_claims_observed"]:
        warnings.append(
            str(data["expired_in_flight_queue_claims_observed"])
            + " actieve wachtrijclaims hebben een verlopen termijn; alleen eigenaar kan herstellen."
        )
    if data["unknown_in_flight_queue_expiry_observed"]:
        warnings.append(
            str(data["unknown_in_flight_queue_expiry_observed"])
            + " actieve wachtrijclaims hebben geen betrouwbaar vervalmoment."
        )
    if not data["source_complete"]:
        warnings.append(
            "Gedeeltelijke of ambigue lokale bron: geen automatische herverdeling."
        )
    if not data["active_cross_lane_coverage_complete"]:
        warnings.append("Actieve cross-lane scopevergelijking is beperkt tot 64 taken.")
    warnings.append(
        "Externe Notion-claims en releasegates zijn niet waargenomen; "
        "dit overzicht verleent geen schrijfrechten."
    )
    details = "".join("<li>" + html.escape(item) + "</li>" for item in warnings)
    portfolio_rows = "".join(
        "<tr><th scope='row'>" + html.escape(item["project"]) + "</th>"
        + "<td>" + str(item["queued_p0"]) + "</td><td>"
        + str(item["queued_p1"]) + "</td><td>"
        + str(item["in_flight_p0"]) + "</td><td>"
        + str(item["in_flight_p1"]) + "</td></tr>"
        for item in data["portfolio_urgent_local"]
    ) or "<tr><td colspan='5'>Geen urgente bronrijen waargenomen; niet gelijk aan geen issues.</td></tr>"
    portfolio_note = (
        "<p class='warn'>Gedeeltelijke projectbron of niet-herkende projectgroep.</p>"
        if data["portfolio_priority_partial"] or data["portfolio_unknown_project_groups"]
        else "<p>Alleen lokale wachtrij, geen live GitHub-issue- of Notion-claimbewijs.</p>"
    )
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
        "table{border-collapse:collapse;width:100%;text-align:right}"
        "th,td{padding:10px 8px;border-bottom:1px solid #30415c}"
        "th:first-child{text-align:left}table{font-size:.9rem}"
        ".scroll{overflow:auto}li{margin:8px 0}"
        "footer{color:#a8b7d4;margin-top:28px}"
        "</style></head><body><main><header><div class='eyebrow'>"
        "Portfolio recovery · operatorweergave</div><h1>Vier herstelbanen</h1>"
        "<p>Actueel lokaal bronmoment: "
        + html.escape(data["observed_utc"]) + "</p></header><div class='grid'>"
        + "".join(cards) + "</div><section class='notice'>"
        "<h2>Portfolio · urgente lokale wachtrij</h2>"
        "<p>Deze tabel is alleen een prioriteitenhint. Bestaande claims en eigenaars blijven leidend.</p>"
        "<div class='scroll'><table><thead><tr><th scope='col'>Project</th>"
        "<th scope='col'>P0 wacht</th><th scope='col'>P1 wacht</th>"
        "<th scope='col'>P0 actief</th><th scope='col'>P1 actief</th>"
        "</tr></thead><tbody>" + portfolio_rows + "</tbody></table></div>"
        + portfolio_note + "</section><section class='notice'><h2>Grenzen en "
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

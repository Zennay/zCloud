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

# Keep normal `python3 scripts/zsentinel_four_lane_desk.py` entrypoints
# working regardless of the invoking directory. In ordinary script execution
# sys.path[0] is scripts/, not necessarily the repo containing lane_generator.
if __name__ == "__main__" and not __package__ and "__file__" in globals():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lane_generator import (
    classify_backlog_item,
    generate_execution_lanes,
    scopes_overlap,
)
from scripts.zsentinel_recovery_actions import suggested_actions
from scripts.zsentinel_runner_presence import observe_local_runners, text_report

LANE_NAMES = {
    "release-integration": "PRs naar main",
    "product-remediation": "Productproblemen",
    "worker-recovery": "Workers en wachtrij",
    "ci-deploy-recovery": "CI en deployments",
}
ACTIVE = {"claimed", "running", "verifying"}
VALID = ACTIVE | {"queued"}
MAX_ROWS = 500
MAX_TITLE = 512
MAX_CRITERIA = 3072
MAX_METADATA = 8192
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


def operator_admission(lane, source_complete, cross_lane_complete, collision_count, expired_local_leases):
    """Map a local lane observation to human-only admission guidance.

    This deliberately never claims a task and is not an admission token.
    Unverified sources, in-flight ownership and scope collisions retain
    precedence over the absence of a queued task.
    """
    state = str(lane.get("status") or "idle").lower()
    deferred = max(0, int(lane.get("deferred_qa_packages") or 0))
    blocked = lane.get("blocked_by") or []
    if state in ACTIVE:
        return {
            "code": "ACTIVE_OWNER_PROTECTED",
            "label": "Bestaande eigenaar behouden",
            "next_step": "Controleer actuele claimhouder en voortgang; neem deze taak niet over.",
        }
    if expired_local_leases and state != "idle":
        return {
            "code": "HOLD_EXPIRED_LOCAL_CLAIM",
            "label": "Verlopen lokale lease(s) aangetroffen",
            "next_step": "Laat de bestaande claimeigenaar de daadwerkelijke taakstatus vergelijken; niet opnieuw claimen.",
        }
    if not source_complete or not cross_lane_complete:
        return {
            "code": "HOLD_SOURCE_RECONCILIATION",
            "label": "Bron- of scopecertificaat ontbreekt",
            "next_step": "Laat de huidige runtime-eigenaar ontbrekende claims, scopes en dekking vaststellen.",
        }
    if collision_count:
        return {
            "code": "HOLD_ACTIVE_SCOPE_CONFLICT",
            "label": "Actieve schrijfscopes overlappen",
            "next_step": "Laat de betrokken bestaande eigenaren het bestandsconflict oplossen.",
        }
    if blocked:
        return {
            "code": "HOLD_EXISTING_CLAIM_OR_SCOPE",
            "label": "Conflict of seriële schrijfgrens",
            "next_step": "Vergelijk actieve leases en bestandsbereiken met de huidige eigenaren.",
        }
    if state == "blocked" and deferred:
        return {
            "code": "HOLD_QA_PRODUCT_POLICY",
            "label": "Alleen uitgesloten QA-werk over",
            "next_step": "Laat de wachtrijeigenaar QA apart beheren; kies echt productwerk.",
        }
    if state == "queued":
        return {
            "code": "CANDIDATE_EXTERNAL_GATES",
            "label": "Lokale kandidaat, niet geautoriseerd",
            "next_step": "Controleer GitHub-PR, Notion-claim, eigenaar en resourcegates vóór start.",
        }
    return {
        "code": "NO_LOCAL_CANDIDATE",
        "label": "Geen lokale kandidaat waargenomen",
        "next_step": "Controleer Notion en GitHub op onbeheerde productissues; claim niets automatisch.",
    }


def collect(database, maximum, include_runner_processes=False):
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
            """SELECT queue_id, project_id,
                      substr(title, 1, 512) AS title,
                      length(title) AS title_size,
                      substr(completion_criteria, 1, 3072) AS completion_criteria,
                      length(completion_criteria) AS criteria_size,
                      priority, status, eligible, created_at, claimed_at, claim_expires,
                      substr(metadata_json, 1, 8192) AS metadata_json,
                      length(metadata_json) AS metadata_size
                 FROM portfolio_queue WHERE project_id=?
                   AND lower(status) IN ('queued','claimed','running','verifying')
                 ORDER BY created_at, queue_id LIMIT ?""",
            ("zsentinel", maximum + 1),
        ).fetchall()
        claim_rows = db.execute(
            """SELECT project_id, lease_until,
                      substr(metadata_json, 1, 8192) AS metadata_json,
                      length(metadata_json) AS metadata_size
                 FROM task_claims WHERE project_id=? LIMIT ?""",
            ("zsentinel", maximum + 1),
        ).fetchall()
        priority_rows = db.execute(
            """SELECT project_id, UPPER(priority) AS priority,
                      LOWER(status) AS status, COUNT(*) AS amount,
                      SUM(CASE WHEN LOWER(status)='queued' AND eligible=1
                               THEN 1 ELSE 0 END) AS queue_eligible
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
        # zSentinel is this desk's role queue, shown by the four lane cards,
        # not an unknown fourteenth product or missing portfolio source.
        if project_id == "zsentinel":
            continue
        if project_id not in PORTFOLIO_PROJECTS:
            unknown_project_groups += 1
            continue
        values = portfolio_counts.setdefault(project_id, {
            "project": PORTFOLIO_PROJECTS[project_id],
            "queued_p0": 0, "queued_p1": 0, "in_flight_p0": 0,
            "in_flight_p1": 0, "eligible_queued_p0": 0, "eligible_queued_p1": 0,
        })
        key = ("queued_" if row["status"] == "queued" else "in_flight_") + row["priority"].lower()
        values[key] += int(row["amount"])
        if row["status"] == "queued":
            values["eligible_queued_" + row["priority"].lower()] += int(
                row["queue_eligible"] or 0
            )
    portfolio = sorted(
        (value for value in portfolio_counts.values() if any(
            value[key] for key in ("queued_p0", "queued_p1", "in_flight_p0", "in_flight_p1")
        )),
        key=lambda v: (-v["queued_p0"], -v["queued_p1"], v["project"]),
    )
    queue = [dict(row) for row in queue_rows[:maximum]]
    truncated_queue_fields = sum(
        (item["title_size"] or 0) > MAX_TITLE
        or (item["criteria_size"] or 0) > MAX_CRITERIA
        or (item["metadata_size"] or 0) > MAX_METADATA
        for item in queue
    )
    # Oversize metadata cannot be trusted for safe conflict admission.
    # We still expose bounded observations, but never infer free ownership.
    now = datetime.now(timezone.utc)
    claims = []
    expired = 0
    ambiguous = 0
    expired_queue_claims = 0
    unknown_queue_claim_expiry = 0
    inconsistent_queue_claim_windows = 0
    runtime_shadowed_active = 0
    for item in queue:
        if str(item.get("status", "")).lower() not in ACTIVE:
            continue
        # The runtime currently filters eligible=1 for all lane inputs.
        if item.get("eligible") != 1:
            runtime_shadowed_active += 1
        expiry = _utc(item.get("claim_expires"))
        claimed = _utc(item.get("claimed_at"))
        # A future expiry cannot certify an invalid start timestamp.
        # Preserve the present owner; only flag the source discrepancy.
        if expiry is None:
            unknown_queue_claim_expiry += 1
        elif claimed is None or claimed > now or expiry <= claimed:
            inconsistent_queue_claim_windows += 1
        elif expiry <= now:
            expired_queue_claims += 1
    for row in claim_rows[:maximum]:
        if (row["metadata_size"] or 0) > MAX_METADATA:
            ambiguous += 1
            continue
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
    unknown_active_scopes = max(
        (lane.get("unknown_active_scope_count", 0) for lane in lanes), default=0
    )
    unknown_claim_scopes = max(
        (lane.get("unknown_claim_scope_count", 0) for lane in lanes), default=0
    )
    untrusted_active_leases = max(
        (lane.get("untrusted_active_lease_count", 0) for lane in lanes), default=0
    )
    # Diagnostic only. The dispatch path itself remains governed by the
    # existing lane generator and actual external claim/release gates.
    active_items = [
        classify_backlog_item(project, item)
        for item in queue if str(item.get("status", "")).lower() in ACTIVE
    ]
    collision_counts = {lane_id: 0 for lane_id in LANE_NAMES}
    cross_lane_coverage = (
        len(active_items) <= 64 and not queue_partial
        and not truncated_queue_fields
        and unknown_active_scopes == 0 and unknown_claim_scopes == 0
    )
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
    # Build source trust once, then apply it consistently in every lane card.
    source_complete = not (
        queue_partial or claims_partial or ambiguous or truncated_queue_fields
        or unknown_active_scopes or unknown_claim_scopes or runtime_shadowed_active
        or portfolio_partial or unknown_project_groups
        or inconsistent_queue_claim_windows or untrusted_active_leases
    )
    visible = []
    for lane in lanes:
        conflicts = lane.get("blocked_by") or []
        admission = operator_admission(
            lane, source_complete, cross_lane_coverage,
            collision_counts[lane["lane_id"]], expired,
        )
        visible.append({
            "id": lane["lane_id"],
            "admission": admission,
            "name": LANE_NAMES[lane["lane_id"]],
            "status": lane["status"],
            "candidate_observed": lane.get("queue_id") is not None,
            "active_backlog_count": lane.get("active_backlog_count", 0),
            "deferred_qa_packages": lane.get("deferred_qa_packages", 0),
            "parallel_active_warning": lane.get("parallel_active_warning", False),
            "serialized_unscoped_queue_admitted": lane.get(
                "serialized_unscoped_queue_admitted", False
            ),
            "new_write_admission_held": (
                admission["code"] != "CANDIDATE_EXTERNAL_GATES"
            ),
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
    packet = {
        "schema": "zsentinel.four-lane-desk.v1",
        "observed_utc": now.replace(microsecond=0).isoformat(),
        "profile": "portfolio-recovery",
        "observed_queue": observed,
        "portfolio_urgent_local": portfolio,
        "portfolio_priority_partial": portfolio_partial,
        "portfolio_unknown_project_groups": unknown_project_groups,
        "queue_partial": queue_partial,
        "truncated_queue_fields_observed": truncated_queue_fields,
        "active_write_scopes_not_declared": unknown_active_scopes,
        "active_claim_scopes_not_declared": unknown_claim_scopes,
        "untrusted_active_leases_observed": untrusted_active_leases,
        "runtime_active_filtered_by_eligibility": runtime_shadowed_active,
        "active_local_leases_observed": len(claims),
        "expired_local_leases_observed": expired,
        "ambiguous_local_claims": ambiguous,
        "claims_partial": claims_partial,
        "expired_in_flight_queue_claims_observed": expired_queue_claims,
        "unknown_in_flight_queue_expiry_observed": unknown_queue_claim_expiry,
        "inconsistent_in_flight_queue_claim_windows_observed": inconsistent_queue_claim_windows,
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
        "source_complete": source_complete,
        "ownership_reconciliation_required": bool(
            expired or expired_queue_claims or unknown_queue_claim_expiry
            or inconsistent_queue_claim_windows or untrusted_active_leases
            or ambiguous or queue_partial or claims_partial
            or truncated_queue_fields or unknown_active_scopes or unknown_claim_scopes
            or runtime_shadowed_active or portfolio_partial or unknown_project_groups
            or not cross_lane_coverage
            or any(collision_counts.values())
            or (sum(observed[s] for s in ACTIVE) > 0 and len(claims) == 0)
        ),
    }
    packet["runner_processes"] = (
        observe_local_runners() if include_runner_processes else None
    )
    packet["recovery_actions"] = suggested_actions(packet)
    return packet


def text_view(data):
    out = [
        "zSentinel · herstelbalie (alleen lezen)",
        "Bron: lokale, begrensde SQLite-snapshot; eigenaarvrijheid NIET afleidbaar.",
    ]
    for lane in data["lanes"]:
        extra = " | MEERDERE ACTIEVE TAKEN" if lane["parallel_active_warning"] else ""
        if lane["deferred_qa_packages"]:
            extra += f" | {lane['deferred_qa_packages']} QA-taken uitgesloten"
        if lane["serialized_unscoped_queue_admitted"]:
            extra += " | MAXIMAAL EEN SCHRIJVER ZONDER SCOPE"
        if lane["new_write_admission_held"]:
            extra += " | NIEUWE SCHRIJFACTIE NIET VRIJGEGEVEN"
        if lane["active_cross_lane_scope_conflicts"]:
            extra += " | CONFLICT MET ANDERE ACTIEVE BAAN"
        elif lane["active_cross_lane_scope_conflicts"] is None:
            extra += " | ACTIEVE SCOPEDEKKING ONVOLLEDIG"
        out.append(
            f"{lane['name']}: {lane['status']}; actieve taken "
            f"{lane['active_backlog_count']}; conflicten "
            f"{lane['conflicting_claims'] + lane['conflicting_queue_entries']}{extra}"
        )
        out.append("  Beslissing: " + lane["admission"]["code"] +
                   " · " + lane["admission"]["label"])
        out.append("  Volgende stap: " + lane["admission"]["next_step"])
    out.append("Herstelacties (probleem / veilige vervolgactie / status):")
    for item in data["recovery_actions"]:
        out.append("  " + item["problem"])
        out.append("  Actie: " + item["next_step"])
        out.append("  Status: " + item["status"])
    if data.get("runner_processes") is not None:
        out.append(text_report(data["runner_processes"]))
    out.append("Open lokale P0/P1-queue per echt project (adviserend):")
    for item in data["portfolio_urgent_local"]:
        out.append(
            f"  {item['project']}: P0 wacht {item['queued_p0']} "
            f"(eligible {item['eligible_queued_p0']}); P1 wacht {item['queued_p1']} "
            f"(eligible {item['eligible_queued_p1']}); "
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
    if data["expired_local_leases_observed"]:
        out.append("HOLD: " + str(data["expired_local_leases_observed"])
                   + " verlopen lokale leases waargenomen; eigenaarstatus handmatig controleren.")
    if data["expired_in_flight_queue_claims_observed"]:
        out.append("VERLOPEN queue-claimtermijnen: "
                   + str(data["expired_in_flight_queue_claims_observed"])
                   + " (niet automatisch herclaimen).")
    if data["unknown_in_flight_queue_expiry_observed"]:
        out.append("Onbekende queue-claimtermijnen: "
                   + str(data["unknown_in_flight_queue_expiry_observed"])
                   + " (controleer eigenaar).")
    if data["untrusted_active_leases_observed"]:
        out.append("HOLD: " + str(data["untrusted_active_leases_observed"])
                   + " actieve taakleases zijn op dit bronmoment onbetrouwbaar; "
                   "nieuwe schrijflanes niet vrijgegeven.")
    if data["inconsistent_in_flight_queue_claim_windows_observed"]:
        out.append("HOLD: " +
                   str(data["inconsistent_in_flight_queue_claim_windows_observed"])
                   + " actieve queue-claimvensters hebben onmogelijke of ontbrekende start; "
                   "behoud huidige eigenaar.")
    if data["active_write_scopes_not_declared"]:
        out.append("HOLD: " + str(data["active_write_scopes_not_declared"])
                   + " actieve taken hebben geen expliciete schrijf-scope; "
                   + "nieuwe parallelle toelating geblokkeerd.")
    if data["active_claim_scopes_not_declared"]:
        out.append("HOLD: " + str(data["active_claim_scopes_not_declared"])
                   + " lokale claims hebben geen expliciete schrijf-scope; "
                   + "geen nieuwe parallelle toelating.")
    if data["runtime_active_filtered_by_eligibility"]:
        out.append(
            "RUNTIME SOURCEGAP: " + str(data["runtime_active_filtered_by_eligibility"])
            + " actieve taken met eligible!=1 worden mogelijk door server.py"
            + " uit de plannerbron gefilterd."
        )
    if data["truncated_queue_fields_observed"]:
        out.append("LET OP: " + str(data["truncated_queue_fields_observed"])
                   + " te grote bronvelden; geen eigenaarvrijheid afleidbaar.")
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
        if lane["deferred_qa_packages"]:
            warning += ("<p class=\u0027warn\u0027>" + str(lane["deferred_qa_packages"])
                        + " wachtende QA-/validatietaken uitgesloten van nieuwe toelating.</p>")
        if lane["serialized_unscoped_queue_admitted"]:
            warning += "<p class='warn'>Wachtende taak zonder schrijfscope: uitsluitend seriële kandidaat, geen claimautorisatie.</p>"
        if lane["new_write_admission_held"]:
            warning += "<p class='warn'>Nieuwe schrijfopdracht niet vrijgegeven.</p>"
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
            + str(count) + "</dd></div></dl>" + warning
            + "<p class='state' style='font-size:1rem'>"
            + html.escape(lane["admission"]["label"]) + "</p>"
            + "<p class='eyebrow'>" + html.escape(lane["admission"]["code"])
            + "</p><p>" + html.escape(lane["admission"]["next_step"])
            + "</p></article>"
        )
    warnings = []
    if data["expired_local_leases_observed"]:
        warnings.append(
            str(data["expired_local_leases_observed"])
            + " verlopen lokale leases waargenomen; controleer de bestaande eigenaar voordat er nieuw werk start."
        )
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
    if data["untrusted_active_leases_observed"]:
        warnings.append(
            str(data["untrusted_active_leases_observed"])
            + " actieve queueleases zijn verlopen of onbetrouwbaar; "
              "behoud huidige eigenaars en geef geen nieuwe schrijver vrij."
        )
    if data["inconsistent_in_flight_queue_claim_windows_observed"]:
        warnings.append(
            str(data["inconsistent_in_flight_queue_claim_windows_observed"])
            + " actieve queue-claimvensters hebben een ontbrekende/toekomstige start of "
              "niet-vooruitlopende vervaltijd; geen herclaim zonder eigenaar."
        )
    if data["active_write_scopes_not_declared"]:
        warnings.append(
            str(data["active_write_scopes_not_declared"])
            + " actieve taken missen een declaratie van bestands-/capability-scope. "
            "Nieuwe parallelle toelating blijft geblokkeerd tot de eigenaar dit oplost."
        )
    if data["active_claim_scopes_not_declared"]:
        warnings.append(
            str(data["active_claim_scopes_not_declared"])
            + " actieve lokale claims missen een declaratie van schrijf-scope. "
            "Geen tweede schrijver toelaten zonder bewijs van externe eigenaars."
        )
    if data["runtime_active_filtered_by_eligibility"]:
        warnings.append(
            str(data["runtime_active_filtered_by_eligibility"])
            + " actieve taken zijn niet eligible en kunnen in de huidige "
            "serverplanner ontbreken. Controleer de bron met de runtime-eigenaar."
        )
    if data["truncated_queue_fields_observed"]:
        warnings.append(
            "Sommige bronvelden overschrijden de grens voor veilig lokaal lezen."
        )
    if not data["source_complete"]:
        warnings.append(
            "Gedeeltelijke of ambigue lokale bron: geen automatische herverdeling."
        )
    if not data["active_cross_lane_coverage_complete"]:
        warnings.append("Actieve cross-lane scopevergelijking is beperkt tot 64 taken.")
    warnings.append(
        "Queue-eligible betekent alleen een lokaal veld, niet dat de taak "
        "claimbaar is: controleer de externe eigenaar en releasegates."
    )
    warnings.append(
        "Externe Notion-claims en releasegates zijn niet waargenomen; "
        "dit overzicht verleent geen schrijfrechten."
    )
    details = "".join("<li>" + html.escape(item) + "</li>" for item in warnings)
    action_cards = "".join(
        "<article><h3>" + html.escape(item["problem"]) + "</h3>"
        + "<p>" + html.escape(item["next_step"]) + "</p>"
        + "<p class='eyebrow'>" + html.escape(item["status"]) + "</p></article>"
        for item in data["recovery_actions"]
    )
    runner_section = ""
    proc = data.get("runner_processes")
    if proc is not None:
        families = "".join(
            "<tr><th scope='row'>" + html.escape(name) + "</th>"
            "<td>" + str(item["listeners"]) + "</td><td>"
            + str(item["workers"]) + "</td><td>"
            + html.escape(item["local_observation"]) + "</td></tr>"
            for name, item in sorted(proc["families"].items())
            if item["listeners"] or item["workers"]
        )
        if not families:
            families = "<tr><td colspan='4'>Geen herkenbare processen. Geen uitvalbewijs.</td></tr>"
        incomplete = (
            "<p class='warn'>Procesbron onvolledig: ontbrekende processen niet bewezen.</p>"
            if not proc["source_complete"] else ""
        )
        runner_section = (
            "<section class='notice'><h2>Lokale GitHub-runners</h2>"
            "<p>Een listenerproces betekent niet dat GitHub een job toewijst, "
            "dat runnerlabels kloppen of dat CI geslaagd is.</p>"
            "<div class='scroll'><table><thead><tr>"
            "<th>Runner</th><th>Listener</th><th>Worker</th><th>Waarneming</th>"
            "</tr></thead><tbody>" + families + "</tbody></table></div>"
            + incomplete + "</section>"
        )
    portfolio_rows = "".join(
        "<tr><th scope='row'>" + html.escape(item["project"]) + "</th>"
        + "<td>" + str(item["queued_p0"]) + " <small>(eligible "
        + str(item["eligible_queued_p0"]) + ")</small></td><td>"
        + str(item["queued_p1"]) + " <small>(eligible "
        + str(item["eligible_queued_p1"]) + ")</small></td><td>"
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
        ".scroll{overflow:auto}li{margin:8px 0}h3{font-size:1rem;line-height:1.4}"
        "footer{color:#a8b7d4;margin-top:28px}"
        "</style></head><body><main><header><div class='eyebrow'>"
        "Portfolio recovery · operatorweergave</div><h1>Vier herstelbanen</h1>"
        "<p>Actueel lokaal bronmoment: "
        + html.escape(data["observed_utc"]) + "</p></header><div class='grid'>"
        + "".join(cards) + "</div><section class='notice'>"
        "<h2>Veilige herstelacties</h2><p>Elke kaart: probleem, actie en status. Alleen advies.</p>"
        "<div class='grid'>" + action_cards + "</div></section><section class='notice'>"
        "<h2>Portfolio · urgente lokale wachtrij</h2>"
        "<p>Deze tabel is alleen een prioriteitenhint. Bestaande claims en eigenaars blijven leidend.</p>"
        "<div class='scroll'><table><thead><tr><th scope='col'>Project</th>"
        "<th scope='col'>P0 wacht</th><th scope='col'>P1 wacht</th>"
        "<th scope='col'>P0 actief</th><th scope='col'>P1 actief</th>"
        "</tr></thead><tbody>" + portfolio_rows + "</tbody></table></div>"
        + portfolio_note + "</section>" + runner_section
        + "<section class='notice'><h2>Grenzen en "
        "vervolgactie</h2><ul>" + details + "</ul></section>"
        "<footer>Alleen lokale read-only bron. Geen claim, CI-start, merge, "
        "runneractie of deployment uitgevoerd.</footer></main></body></html>"
    )


def markdown_view(data):
    """Portable private owner handoff: three lines per action, no raw queue fields."""
    lines = [
        "# zSentinel | Hersteloverdracht",
        "",
        "**Lokale bron:** " + data["observed_utc"],
        "**Context:** alleen read-only wachtrijobservatie; Notion-claims en"
        " GitHub-releasegates NIET gecontroleerd.",
        "",
        "## Actiekaarten",
    ]
    for lane in data["lanes"]:
        lines.extend((
            "",
            "### " + lane["name"],
            "- **Bronstatus:** " + lane["status"],
            "- **Beslissing:** " + lane["admission"]["code"]
            + " — " + lane["admission"]["label"],
            "- **Vervolgactie:** " + lane["admission"]["next_step"],
            "- **Uitgestelde QA-opdrachten:** " + str(lane["deferred_qa_packages"]),
        ))
    lines.extend(("", "## Geprioriteerde herstelacties"))
    for action in data["recovery_actions"]:
        lines.extend((
            "",
            "- **Probleem:** " + action["problem"],
            "  **Actie:** " + action["next_step"],
            "  **Status:** " + action["status"],
        ))
    if data.get("runner_processes") is not None:
        lines.extend(("", "## Runnerprocessen (alleen /proc)", "",
                      text_report(data["runner_processes"]), ""))
    lines.extend(("", "## Urgente portfolio-queue (lokale bron)", "",
                  "| Project | P0 wacht | P1 wacht | P0 actief | P1 actief |",
                  "| --- | ---: | ---: | ---: | ---: |"))
    for item in data["portfolio_urgent_local"]:
        lines.append(
            f"| {item['project']} | {item['queued_p0']} "
            f"(eligible {item['eligible_queued_p0']}) | {item['queued_p1']} "
            f"(eligible {item['eligible_queued_p1']}) | "
            f"{item['in_flight_p0']} | {item['in_flight_p1']} |"
        )
    lines.extend((
        "",
        "## Integriteit en bevoegdheden",
        "",
        "- Lokale bron volledig: "
        + ("ja" if data["source_complete"] else "nee/onbekend"),
        "- Actieve queueleases niet toelaatbaar voor nieuwe schrijvers: "
        + str(data["untrusted_active_leases_observed"])
        + " (houder blijft eigenaar).",
        "- Onbetrouwbare actieve queue-claimvensters: "
        + str(data["inconsistent_in_flight_queue_claim_windows_observed"])
        + " (geen automatische herclaim).",
        "- Actuele externe eigenaarvrijheid: NIET AFLEIDBAAR.",
        "- De term eligible betekent uitsluitend het lokale wachtrijveld.",
        "- Geen claim, serviceherstart, workflow, merge, release of deploy geautoriseerd.",
        "- Source-read is geen bevestiging van GitHub merged=true of een hervatte worker.",
        "",
    ))
    return "\n".join(lines)


def save_private(path, markup, extension=".html"):
    dest = Path(path)
    if dest.suffix.lower() != extension or dest.name in ("", ".", ".."):
        raise ValueError("New private report with requested extension required")
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
    parser.add_argument("--runner-processes", action="store_true",
                        help="Read only local /proc listener presence; no job or online proof")
    parser.add_argument("--format", choices=("text", "json", "html", "markdown"), default="text")
    parser.add_argument("--output", help="New owner-private file: .html or .md")
    args = parser.parse_args()
    if not 1 <= args.max_rows <= MAX_ROWS:
        parser.error("--max-rows must be between 1 and 500")
    if (args.format in ("html", "markdown")) != bool(args.output):
        parser.error("HTML/Markdown requires --output, other formats cannot write files")
    try:
        packet = collect(args.db, args.max_rows,
                         include_runner_processes=args.runner_processes)
        if args.format == "html":
            save_private(args.output, html_view(packet), ".html")
            print("Privé herstelbalie gemaakt (0600); geen systeemactie.")
        elif args.format == "markdown":
            save_private(args.output, markdown_view(packet), ".md")
            print("Privé hersteloverdracht gemaakt (0600); geen systeemactie.")
        elif args.format == "json":
            print(json.dumps(packet, ensure_ascii=False, sort_keys=True))
        else:
            print(text_view(packet), end="")
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
        parser.exit(1, "Geen betrouwbaar lokaal bronoverzicht; geen actie.\n")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Read-only recovery routing transitions for existing private portfolio snapshots.

A disappearance from bounded samples never proves resolution, ownership
release, CI completion or permission to claim, retry, merge or dispatch.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from html import escape

SCHEMA = "zsentinel-routing-transition-v1"
IDENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
PRIORITY = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
KINDS = {
    "suggested_only_no_claim": "advisory_suggestion",
    "incident_dependency_reconciliation_required": "incident_dependency_hold",
    "required_gate_blocked": "gate_hold",
    "self_hosted_capacity_not_advisory_headroom": "gate_hold",
    "compute_destination_not_explicitly_routable": "gate_hold",
    "overlapping_suggested_scope": "shortlist_hold",
    "shortlist_limit_reached": "shortlist_hold",
}
COVERAGE = {"complete", "incomplete_or_stale"}


def _clock(value):
    if type(value) is not str or len(value) > 45:
        raise ValueError("invalid routing observation UTC")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid routing observation UTC") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("routing observation needs timezone")
    return parsed.astimezone(timezone.utc)


def _decision(snapshot):
    if type(snapshot) is not dict:
        raise ValueError("invalid private routing snapshot")
    if "source_bound_decision" not in snapshot:
        # Older handoff snapshots are not a claim source.
        return None
    portfolio = snapshot.get("portfolio")
    data = snapshot["source_bound_decision"]
    if (type(portfolio) is not dict
            or portfolio.get("schema") != "zsentinel-portfolio-overview-v1"
            or portfolio.get("queue_owner_authorized") is not False
            or portfolio.get("actions_performed") is not False
            or type(data) is not dict or type(data.get("schema_version")) is not int
            or data["schema_version"] != 1
            or data.get("mutations_performed") is not False
            or data.get("queue_claims_changed") is not False
            or data.get("ci_retried") is not False
            or data.get("deployment_authorized") is not False
            or data.get("watch_delivery_performed") is not False):
        raise ValueError("untrusted or authorizing routing snapshot")
    if data.get("observed_at_utc") != portfolio.get("observed_at_utc"):
        raise ValueError("routing observation does not match portfolio source")
    return data


def _index(data):
    rows = {}
    counts = {}
    complete = data.get("routing_inventory_complete") is True
    observed = _clock(data.get("observed_at_utc"))
    for field, kind in (
        ("incident_dependency_holds", "incident_dependency_hold"),
        ("routing_gate_holds", "gate_hold"),
        ("routing_selection_holds", "shortlist_hold"),
    ):
        receipt = data.get(field)
        if receipt is None:
            complete = False
            counts[kind] = None
            continue
        if (type(receipt) is not dict
                or receipt.get("coverage") not in COVERAGE
                or type(receipt.get("count")) is not int
                or not 0 <= receipt["count"] <= 250
                or type(receipt.get("examples")) is not list
                or len(receipt["examples"]) > 8
                or len(receipt["examples"]) > receipt["count"]
                or receipt.get("samples_truncated") is not
                    (receipt["count"] > len(receipt["examples"]))
                or receipt.get("claim_authorized") is not False
                or (kind != "incident_dependency_hold"
                    and receipt.get("dispatch_authorized") is not False)):
            raise ValueError("untrusted routing HOLD source")
        if receipt["coverage"] != "complete":
            complete = False
        counts[kind] = receipt["count"] if receipt["coverage"] == "complete" else None
        for row in receipt["examples"]:
            _insert(rows, row, kind)
    offered = data.get("independent_task_suggestions")
    if type(offered) is not list or len(offered) > 5:
        raise ValueError("untrusted routing shortlist")
    for row in offered:
        _insert(rows, row, "advisory_suggestion")
    counts["advisory_suggestion"] = len(offered) if complete else None
    # Any truncated HOLD category can hide identities and changes. Never
    # mistake the visible subset for a complete routing inventory.
    truncated = any(type(data.get(field)) is dict and
                    data[field].get("samples_truncated") is True
                    for field in ("incident_dependency_holds",
                                  "routing_gate_holds", "routing_selection_holds"))
    return {"observed": observed, "rows": rows, "counts": counts,
            "complete": complete, "sample_truncated": truncated}


def _insert(index, row, category):
    if type(row) is not dict:
        raise ValueError("invalid routing transition item")
    pid, ident = row.get("project_id"), row.get("id")
    tier = row.get("priority")
    if (type(pid) is not str or not IDENT.fullmatch(pid)
            or type(ident) is not str or not IDENT.fullmatch(ident)
            or tier not in PRIORITY):
        raise ValueError("invalid routing transition identity")
    key = (pid, ident)
    if key in index:
        raise ValueError("conflicting routing states for same task")
    reason = row.get("reason") if category != "advisory_suggestion" else "suggested_only_no_claim"
    if reason not in KINDS or KINDS[reason] != category:
        raise ValueError("invalid routing transition reason")
    if category == "advisory_suggestion":
        if row.get("status") != "suggested_only_no_claim":
            raise ValueError("unexpected routing suggestion status")
    elif row.get("claim_authorized") is not False:
        raise ValueError("HOLD is not a claim")
    if category in ("gate_hold", "shortlist_hold") and row.get("dispatch_authorized") is not False:
        raise ValueError("HOLD is not dispatch authority")
    index[key] = {"project_id": pid, "id": ident, "priority": tier,
                  "state": category, "reason": reason,
                  "claim_authorized": False, "dispatch_authorized": False}


def compare(previous, current, *, now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("timezone-aware comparison time required")
    newer = _decision(current)
    if newer is None:
        raise ValueError("current report must include source-bound routing")
    new = _index(newer)
    if new["observed"] > now + timedelta(minutes=2):
        raise ValueError("future current routing observation")
    older = _decision(previous)
    if older is None:
        return _result("previous_routing_not_supplied", new,
                       previous_at=None, fresh=False)
    old = _index(older)
    if (old["observed"] >= new["observed"]
            or old["observed"] < new["observed"] - timedelta(hours=72)):
        return _result("previous_routing_too_old_or_out_of_order", new,
                       previous_at=old["observed"], fresh=False)
    # A saved report is only a source snapshot, never proof of current
    # ownership. Comparisons are still informative when a source is partial,
    # but all disappearances must remain explicitly not-reobserved.
    created, persisting, transitioned, missing = [], [], [], []
    for key, value in sorted(new["rows"].items(), key=lambda pair: (
            PRIORITY[pair[1]["priority"]], pair[0])):
        prior = old["rows"].get(key)
        if prior is None:
            created.append(value)
        elif (prior["state"], prior["reason"], prior["priority"]) != (
                value["state"], value["reason"], value["priority"]):
            transitioned.append({
                "project_id": key[0], "id": key[1],
                "priority": value["priority"],
                "before": prior["state"], "after": value["state"],
                "before_reason": prior["reason"], "after_reason": value["reason"],
                "owner_recheck_required": True, "claim_authorized": False,
            })
        else:
            persisting.append(value)
    for key, value in sorted(old["rows"].items(), key=lambda pair: (
            PRIORITY[pair[1]["priority"]], pair[0])):
        if key not in new["rows"]:
            missing.append(value)
    return {
        **_result("bounded_source_comparison", new,
                  previous_at=old["observed"], fresh=True),
        "prior_coverage_complete": old["complete"],
        "previous_source_examples_truncated": old["sample_truncated"],
        "newly_observed": created[:12],
        "unchanged_observed": persisting[:12],
        "state_transitions": transitioned[:12],
        "not_reobserved_not_resolved": missing[:12],
        "newly_observed_count": len(created),
        "state_transition_count": len(transitioned),
        "not_reobserved_count": len(missing),
        "samples_truncated": any(len(rows) > 12 for rows in
                                 (created, persisting, transitioned, missing)),
        "task_released_verified": False,
        "issue_closed_verified": False,
        "owner_claim_verified": False,
        "merge_authorized": False,
        "actions_performed": False,
    }


def _result(coverage, newer, previous_at, fresh):
    stamp = lambda value: value.isoformat(timespec="seconds").replace("+00:00", "Z") if value else None
    return {
        "schema": SCHEMA, "coverage": coverage,
        "observed_at_utc": stamp(newer["observed"]),
        "previous_observed_at_utc": stamp(previous_at),
        "current_coverage_complete": newer["complete"],
        "current_source_examples_truncated": newer["sample_truncated"],
        "current_state_counts": newer["counts"],
        "newly_observed": [], "unchanged_observed": [],
        "state_transitions": [], "not_reobserved_not_resolved": [],
        "newly_observed_count": None, "state_transition_count": None,
        "not_reobserved_count": None, "samples_truncated": False,
        "prior_coverage_complete": False,
        "previous_source_examples_truncated": False,
        "task_released_verified": False, "issue_closed_verified": False,
        "owner_claim_verified": False, "merge_authorized": False,
        "actions_performed": False,
    }


def as_text(report):
    if (type(report) is not dict or report.get("schema") != SCHEMA
            or report.get("actions_performed") is not False
            or report.get("merge_authorized") is not False):
        raise ValueError("untrusted routing comparison")
    lines = ["zSentinel · routeringsveranderingen (alleen advies)"]
    if report["coverage"] != "bounded_source_comparison":
        lines.append("Vorige routeringsbron ontbreekt of is onvergelijkbaar; veranderingen ONBEKEND.")
        return "\n".join(lines)
    lines.append("Bronmomenten: " + report["previous_observed_at_utc"] +
                 " → " + report["observed_at_utc"])
    lines.append("Nieuwe waarnemingen: " + str(report["newly_observed_count"]) +
                 " · gewijzigde route: " + str(report["state_transition_count"]) +
                 " · niet meer in steekproef: " + str(report["not_reobserved_count"]))
    if not report["prior_coverage_complete"] or not report["current_coverage_complete"]:
        lines.append("Bron ONVOLLEDIG; aantallen zijn geen volledig werkoverzicht.")
    for row in report["state_transitions"]:
        lines.append("- " + row["priority"] + " " + row["project_id"] + "/" +
                     row["id"] + " · " + row["before_reason"] + " → " +
                     row["after_reason"] + " · eigenaar verifieert huidige bron; geen claim.")
    for row in report["not_reobserved_not_resolved"]:
        lines.append("- NIET MEER WAARGENOMEN " + row["priority"] + " " +
                     row["project_id"] + "/" + row["id"] +
                     " · geen herstel, sluiting of vrijgave bewezen.")
    if report["samples_truncated"] or report["current_source_examples_truncated"] or report["previous_source_examples_truncated"]:
        lines.append("Voorbeelden afgekapt; verwijderde of ongetoonde taken blijven ONBEKEND.")
    return "\n".join(lines)


def as_html(report):
    visible = as_text(report)
    lines = visible.splitlines()
    return ('<section class="panel read"><h2>Routeringsveranderingen · brongebonden</h2>'
            '<p class="muted">Afwezig in een volgende steekproef betekent nooit opgelost '
            'of vrijgegeven. Er zijn geen queue-, worker- of releaseacties uitgevoerd.</p>'
            '<ul>' + "".join('<li>' + escape(line) + '</li>' for line in lines[1:]) +
            '</ul></section>')

#!/usr/bin/env python3
"""Read a private, human-saved Notion connector query snapshot, never a claim.

This adapter cannot authenticate the person who saved the JSON. It presents
positive *observed* active-status hints only, not owner/lease/dispatch proof.
No network, Notion token, control-plane import, queue writer or persistence.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from zsentinel_recovery_router import read_json
from zsentinel_notion_claim_observer import project_labels

SCHEMA = "zsentinel-notion-connector-snapshot-v1"
UUID = re.compile(r"^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$", re.I)
PROJECT_URL = re.compile(r"^https://app[.]notion[.]com/(?:p/)?([a-f0-9]{32})$", re.I)
ACTIVE = frozenset(("Claimed", "Running"))
MAX_BYTES = 131_072
MAX_ROWS = 100
UTC = timezone.utc


def _datetime(value):
    if not isinstance(value, str) or len(value) > 45:
        raise ValueError("invalid captured Notion observation time")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("invalid captured Notion observation time") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Notion observation must be timezone aware")
    return parsed.astimezone(UTC)


def _project(value, known):
    # A source relation comes from the Notion connector as one serialized URL.
    # Never infer ownership from titles, worker names or an unrecognized URL.
    if not isinstance(value, str) or len(value) > 250:
        return "onbekend"
    try:
        links = json.loads(value)
    except (ValueError, TypeError):
        return "onbekend"
    if not isinstance(links, list) or len(links) != 1 or not isinstance(links[0], str):
        return "onbekend"
    matched = PROJECT_URL.fullmatch(links[0])
    return known.get(matched.group(1).lower(), "onbekend") if matched else "onbekend"


def observe_saved_connector(source_file: Path, data_source_id: str,
                            known: dict, now=None):
    """Return privacy-minimal active-status lower bounds for this one snapshot.

    Reject missing pages and duplicate Work IDs. Snapshot fields may be
    manually forged, so none of these values may authorize release/claims.
    """
    if not isinstance(data_source_id, str) or not UUID.fullmatch(data_source_id):
        raise ValueError("Notion data-source UUID required for saved connector import")
    if not isinstance(known, dict):
        raise ValueError("canonical project mapping required")
    source = read_json(source_file, MAX_BYTES, "private Notion connector export")
    if (type(source) is not dict or source.get("schema") != SCHEMA
            or source.get("data_source_id", "").lower() != data_source_id.lower()
            or type(source.get("response")) is not dict):
        raise ValueError("Notion snapshot wrapper/source identity mismatch")
    captured = _datetime(source.get("observed_at_utc"))
    moment = now if now is not None else datetime.now(UTC)
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("current clock lacks timezone")
    age = (moment.astimezone(UTC) - captured).total_seconds()
    if not -60 <= age <= 600:
        raise ValueError("Notion snapshot expired or from the future")
    response = source["response"]
    ids = response.get("data_source_ids")
    rows = response.get("results")
    if (type(response.get("has_more")) is not bool
            or response["has_more"] is not False
            or type(ids) is not list or ids != [data_source_id]
            or type(rows) is not list or len(rows) > MAX_ROWS):
        raise ValueError("Notion snapshot incomplete or not bound to exact source")
    seen, active, counts = set(), [], {}
    for row in rows:
        if type(row) is not dict or type(row.get("Work ID")) is not int:
            raise ValueError("invalid Notion work record")
        work = row["Work ID"]
        if not 0 < work < 10**9 or work in seen:
            raise ValueError("duplicate/out-of-range Notion work identity")
        seen.add(work)
        status = row.get("Status")
        if status not in ACTIVE:
            # Filtering to active Notion statuses is mandatory, not optional.
            raise ValueError("Notion snapshot not restricted to active work")
        label = _project(row.get("Project"), known)
        counts[label] = counts.get(label, 0) + 1
        active.append({"work": f"PWQ-{work}", "project": label, "status": status})
    active.sort(key=lambda r: (r["project"], r["work"]))
    return {
        "source": "user_supplied_notion_connector_export_unverified_origin",
        "observed_at": captured.isoformat().replace("+00:00", "Z"),
        "coverage": "saved_single_page_not_live_current_claims_unknown",
        "active_count": len(active),
        "active_project_counts": dict(sorted(counts.items())),
        "items": active[:30],
        "items_truncated": len(active) > 30,
        "claim_scope_verified": False,
        "task_inventory_verified": False,
        "can_dispatch": False,
        "can_release_claim": False,
        "origin_authenticity_verified": False,
        "live_claim_ownership_verified": False,
        "snapshot_max_age_seconds": 600,
    }

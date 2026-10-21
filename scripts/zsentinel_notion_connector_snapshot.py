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

SCHEMA = "zsentinel-notion-connector-snapshot-v1"
UUID = re.compile(r"^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$", re.I)
PROJECT_URL = re.compile(
    r"^https://app[.]notion[.]com/(?:p/)?([a-f0-9]{32})(?:[?]pvs=[1-9][0-9]{0,3})?$", re.I
)
WORK_ID = re.compile(r"^PWQ-([1-9][0-9]{0,8})$")
ACTIVE = frozenset(("Claimed", "Running", "Verifying"))
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
    # Notion connector exports can preserve the relation as a JSON list or
    # serialize that exact list to a string. Accept neither titles nor
    # unbounded aliases: identity must be one canonical project page URL.
    if isinstance(value, str):
        if len(value) > 500:
            return "onbekend"
        try:
            links = json.loads(value)
        except (ValueError, TypeError):
            return "onbekend"
    else:
        links = value
    if not isinstance(links, list) or len(links) != 1 or not isinstance(links[0], str):
        return "onbekend"
    matched = PROJECT_URL.fullmatch(links[0])
    return known.get(matched.group(1).lower(), "onbekend") if matched else "onbekend"


def _work_number(value):
    # A Notion number property is sometimes emitted as an integer and
    # sometimes rendered by the connector as the prefixed Work ID. Never
    # strip arbitrary letters or silently accept zero-padded/float IDs.
    if type(value) is int:
        return value if 0 < value < 10**9 else None
    if isinstance(value, str):
        matched = WORK_ID.fullmatch(value)
        return int(matched.group(1)) if matched else None
    return None


def _native_row(row, known, data_source_id):
    """Normalize one genuine Notion API property payload without titles.

    This remains an unverified, locally supplied source. A malformed source
    row cannot become a positive claim or a dispatch/ownership permission.
    """
    properties = row.get("properties")
    page_id = row.get("id")
    parent = row.get("parent")
    if (type(properties) is not dict or not isinstance(page_id, str)
            or not UUID.fullmatch(page_id)
            or type(parent) is not dict
            or parent.get("type") != "data_source_id"
            or type(parent.get("data_source_id")) is not str
            or parent["data_source_id"].lower() != data_source_id.lower()
            or any(key in row for key in ("Work ID", "Status", "Project"))):
        raise ValueError("ambiguous or invalid native Notion row")
    work_prop = properties.get("Work ID")
    status_prop = properties.get("Status")
    project_prop = properties.get("Project")
    if (type(work_prop) is not dict or work_prop.get("type") not in ("number", "unique_id")
            or type(status_prop) is not dict
            or status_prop.get("type") not in ("select", "status")
            or type(status_prop.get(status_prop["type"])) is not dict):
        raise ValueError("native Notion row missing typed work/status properties")
    if work_prop["type"] == "unique_id":
        unique = work_prop.get("unique_id")
        if (type(unique) is not dict or unique.get("prefix") != "PWQ"):
            raise ValueError("unexpected native Notion Work ID prefix")
        work = _work_number(unique.get("number"))
    else:
        work = _work_number(work_prop.get("number"))
    status = status_prop[status_prop["type"]].get("name")
    if (type(project_prop) is not dict or project_prop.get("type") != "relation"
            or project_prop.get("has_more") is True):
        # Never infer a project from titles or a truncated relation.
        label = "onbekend"
    else:
        relations = project_prop.get("relation")
        if (type(relations) is not list or len(relations) != 1
                or type(relations[0]) is not dict
                or not isinstance(relations[0].get("id"), str)
                or not UUID.fullmatch(relations[0]["id"])):
            label = "onbekend"
        else:
            label = known.get(relations[0]["id"].replace("-", "").lower(), "onbekend")
    return work, status, label, page_id.lower()


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
            or not isinstance(source.get("data_source_id"), str)
            or source["data_source_id"].lower() != data_source_id.lower()
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
    # Saved connector queries carry data_source_ids. Unmodified Notion REST
    # query responses instead use object=list and per-page parent identity;
    # permit that path only for nonempty exclusively native pages, never for
    # an empty source that could misleadingly imply nobody is active.
    connector_identity = type(ids) is list and ids == [data_source_id]
    native_identity = (ids is None and response.get("object") == "list"
                       and type(rows) is list and bool(rows)
                       and all(type(row) is dict and "properties" in row
                               for row in rows))
    if (type(response.get("has_more")) is not bool
            or response["has_more"] is not False
            or not (connector_identity or native_identity)
            or type(rows) is not list or len(rows) > MAX_ROWS
            or response.get("next_cursor") not in (None, "")):
        # An old or cursor-continued page is never a complete claim snapshot.
        raise ValueError("Notion snapshot incomplete or not bound to exact source")
    seen, page_ids, active, counts, verifying_counts = set(), set(), [], {}, {}
    for row in rows:
        if type(row) is not dict:
            raise ValueError("invalid Notion work record")
        if "properties" in row:
            work, status, label, page_id = _native_row(row, known, data_source_id)
            if page_id in page_ids:
                raise ValueError("duplicate native Notion page identity")
            page_ids.add(page_id)
        else:
            work = _work_number(row.get("Work ID"))
            status = row.get("Status")
            label = _project(row.get("Project"), known)
        if work is None or work in seen:
            raise ValueError("duplicate/out-of-range Notion work identity")
        seen.add(work)
        if status not in ACTIVE:
            # Filtering to active Notion statuses is mandatory, not optional.
            raise ValueError("Notion snapshot not restricted to active work")
        counts[label] = counts.get(label, 0) + 1
        if status == "Verifying":
            verifying_counts[label] = verifying_counts.get(label, 0) + 1
        active.append({"work": f"PWQ-{work}", "project": label, "status": status})
    active.sort(key=lambda r: (r["project"], r["work"]))
    return {
        "source": "user_supplied_notion_connector_export_unverified_origin",
        "observed_at": captured.isoformat().replace("+00:00", "Z"),
        "coverage": "saved_single_page_not_live_current_claims_unknown",
        "active_count": len(active),
        "active_project_counts": dict(sorted(counts.items())),
        "verifying_project_counts": dict(sorted(verifying_counts.items())),
        "verifying_count": sum(verifying_counts.values()),
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

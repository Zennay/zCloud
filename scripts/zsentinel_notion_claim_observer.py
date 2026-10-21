#!/usr/bin/env python3
"""Bounded read-only Notion claim visibility for the private zSentinel cockpit.

The Notion data-source query is a read operation despite its POST transport.
Never infer file ownership, lease release, eligibility or worker recovery from
page properties. Token, page IDs and raw relation targets are never emitted.
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

UUID = re.compile(r"^(?:[0-9a-f]{32}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})$", re.I)
MAX_PAGES = 4
PAGE_SIZE = 100
MAX_BODY = 600_000
ACTIVE = frozenset(("Claimed", "Running", "Verifying"))
NOTION_PROJECT_URL = re.compile(r"^https://app[.]notion[.]com/p/([0-9a-f]{32})(?:[?].*)?$", re.I)


def project_labels(registry_path: Path, canonical: dict):
    """Bind Notion relations to the SAME canonical GitHub project identities.

    The independent second read previously permitted unbounded, duplicate-key
    JSON and silently skipped mismatched registry entries. An incomplete
    mapping could hide a live worker claim behind an UNKNOWN project.
    """
    from zsentinel_product_issue_inbox import canonical_projects, MAX_CONFIG_BYTES
    from zsentinel_recovery_router import read_json
    if (not isinstance(registry_path, Path) or not isinstance(canonical, dict)
            or not canonical or len(canonical) > 18):
        raise ValueError("canonical project registry unavailable")
    entries = read_json(registry_path, MAX_CONFIG_BYTES, "Notion project register")
    if not isinstance(entries, list):
        raise ValueError("invalid canonical project registry")
    # Pin this relation map to the same GitHub project set already admitted
    # by the issue/PR collector, rather than accepting another registry.
    if canonical_projects(registry_path) != canonical:
        raise ValueError("Notion registry no longer matches canonical GitHub scope")
    result = {}
    seen_projects = set()
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("id") not in canonical:
            raise ValueError("unregistered Notion relation project")
        slug, url = entry["id"], entry.get("notion_url")
        match = NOTION_PROJECT_URL.fullmatch(url) if isinstance(url, str) else None
        if not match or slug in seen_projects:
            raise ValueError("invalid or duplicate project Notion relation")
        key = match.group(1).lower()
        if key in result:
            raise ValueError("duplicate Notion project mapping")
        result[key] = slug
        seen_projects.add(slug)
    if seen_projects != set(canonical):
        raise ValueError("Notion project relation scope incomplete")
    return result


def _project_name(prop, known):
    if not isinstance(prop, dict) or prop.get("type") != "relation" or prop.get("has_more") is True:
        return "onbekend"
    ids = prop.get("relation")
    if not isinstance(ids, list) or len(ids) != 1 or not isinstance(ids[0], dict):
        return "onbekend"
    target = ids[0].get("id")
    if not isinstance(target, str) or not UUID.fullmatch(target):
        return "onbekend"
    return known.get(target.replace("-", "").lower(), "niet in projectregister")



class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Notion response redirected")


def _text(prop):
    if not isinstance(prop, dict):
        return ""
    kind = prop.get("type")
    parts = prop.get(kind) if kind in ("title", "rich_text") else None
    if not isinstance(parts, list):
        return ""
    return " ".join(p.get("plain_text", "") for p in parts
                    if isinstance(p, dict) and isinstance(p.get("plain_text"), str))[:120]


def _state(prop):
    if not isinstance(prop, dict):
        return ""
    value = prop.get(prop.get("type"))
    return value.get("name", "") if isinstance(value, dict) else ""


def _work_number(prop):
    if not isinstance(prop, dict):
        return None
    value = prop.get(prop.get("type"))
    if isinstance(value, dict):
        value = value.get("number")
    return value if type(value) is int and 0 < value < 10**9 else None



def fair_active_samples(rows, limit=30):
    """Bound visible examples fairly across the three observed work statuses.

    The underlying per-project counts still use *all* observed rows. This
    cannot prove completeness, unlock a claim, or infer a current worker.
    """
    if (type(rows) is not list or len(rows) > MAX_PAGES * PAGE_SIZE
            or type(limit) is not int or not 1 <= limit <= 30):
        raise ValueError("invalid bounded Notion sample request")
    buckets = {status: [] for status in ("Claimed", "Running", "Verifying")}
    for row in rows:
        if type(row) is not dict or row.get("status") not in buckets:
            raise ValueError("invalid Notion sampled work status")
        buckets[row["status"]].append(row)
    offsets = {status: 0 for status in buckets}
    visible = []
    while len(visible) < limit:
        added = False
        for status in buckets:
            if offsets[status] < len(buckets[status]):
                visible.append(buckets[status][offsets[status]])
                offsets[status] += 1
                added = True
                if len(visible) == limit:
                    break
        if not added:
            break
    return visible


def observe_claims(data_source_id, token, known_projects=None):
    if not isinstance(data_source_id, str) or not UUID.fullmatch(data_source_id):
        raise ValueError("Notion claim data source must be a canonical UUID")
    if not isinstance(token, str) or not token or len(token) > 512 or "\n" in token or "\r" in token:
        raise ValueError("Notion read token unavailable or invalid")
    if known_projects is None:
        known_projects = {}
    if not isinstance(known_projects, dict):
        raise ValueError("invalid Notion project mapping")
    endpoint = "https://api.notion.com/v1/data_sources/" + data_source_id + "/query"
    rows = []
    seen_pages = set()
    incomplete = False
    status_source_windows = []
    opener = urllib.request.build_opener(NoRedirect())
    # Reserve separate bounded Notion source windows. A large historic
    # Verifying backlog must not crowd out fresh Claimed/Running rows.
    # Total request budget remains MAX_PAGES (four HTTP reads).
    status_windows = (("Claimed", 1), ("Running", 1), ("Verifying", 2))
    if sum(budget for _, budget in status_windows) != MAX_PAGES:
        raise ValueError("invalid Notion source window budget")
    for requested_status, page_budget in status_windows:
        cursor = None
        seen_cursors = set()
        status_pages = 0
        status_matched = 0
        status_partial = False
        for index in range(page_budget):
            payload = {
                "page_size": PAGE_SIZE,
                "filter": {"property": "Status",
                           "select": {"equals": requested_status}},
                "sorts": [{"timestamp": "last_edited_time",
                           "direction": "descending"}],
            }
            if cursor is not None:
                payload["start_cursor"] = cursor
            request = urllib.request.Request(
                endpoint,
                data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
                headers={
                    "Authorization": "Bearer " + token,
                    "Notion-Version": "2025-09-03",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                method="POST",
            )
            try:
                with opener.open(request, timeout=8) as response:
                    if response.status != 200:
                        raise ValueError("Notion source unavailable")
                    raw = response.read(MAX_BODY + 1)
            except (urllib.error.URLError, TimeoutError, OSError):
                raise ValueError("Notion claim source unavailable") from None
            if len(raw) > MAX_BODY:
                raise ValueError("Notion claim source over size limit")
            try:
                data = json.loads(raw.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError):
                raise ValueError("Notion response is not valid UTF-8 JSON") from None
            if (not isinstance(data, dict) or not isinstance(data.get("results"), list)
                    or len(data["results"]) > PAGE_SIZE):
                raise ValueError("Invalid Notion claim page")
            status_pages += 1
            for page in data["results"]:
                if not isinstance(page, dict) or not isinstance(page.get("properties"), dict):
                    raise ValueError("Malformed Notion claim record")
                page_id = page.get("id")
                if (not isinstance(page_id, str) or not UUID.fullmatch(page_id)
                        or page_id.lower() in seen_pages):
                    # Duplicate across status windows is a changing source, not
                    # permission to assign one item twice or release a lease.
                    raise ValueError("Notion work identity changed across source windows")
                seen_pages.add(page_id.lower())
                props = page["properties"]
                status_prop = props.get("Status")
                if (not isinstance(status_prop, dict)
                        or status_prop.get("type") not in ("select", "status")):
                    raise ValueError("Missing or incompatible Notion Status property")
                state = _state(status_prop)
                if not isinstance(state, str):
                    raise ValueError("Invalid Notion work status type")
                if state != requested_status:
                    # An item changed state between Notion's filter and
                    # delivery. This window cannot claim completeness.
                    incomplete = True
                    status_partial = True
                    continue
                status_matched += 1
                title = _text(props.get("Work item")) or "Onbenoemd actief werk"
                number = _work_number(props.get("Work ID"))
                rows.append({
                    "work": ("PWQ-" + str(number) if number else "Werk-ID onbekend"),
                    "title": title, "status": state,
                    "project": _project_name(props.get("Project"), known_projects),
                })
            more = data.get("has_more")
            if type(more) is not bool:
                raise ValueError("Invalid Notion pagination status")
            if not more:
                break
            cursor = data.get("next_cursor")
            if (not isinstance(cursor, str) or not 1 <= len(cursor) <= 200
                    or cursor in seen_cursors):
                raise ValueError("Invalid Notion pagination cursor")
            seen_cursors.add(cursor)
            if index == page_budget - 1:
                incomplete = True
                status_partial = True
        status_source_windows.append({
            "status": requested_status,
            "pages_read": status_pages,
            "page_budget": page_budget,
            "observed_rows": status_matched,
            "coverage": "partial" if status_partial else "complete_within_source",
            "read_only": True,
            "claim_scope_verified": False,
        })
    counts, verifying_counts = {}, {}
    for row in rows:
        label = row["project"]
        counts[label] = counts.get(label, 0) + 1
        if row["status"] == "Verifying":
            verifying_counts[label] = verifying_counts.get(label, 0) + 1
    return {
        "source": "notion_readonly_data_source_pages",
        "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "coverage": "incomplete" if incomplete else "current_pages_only",
        "active_count": len(rows),
        # Claimed/Running/Verifying are possible work-occupancy signals,
        # not proof of file-level claims or active worker leases.
        # All bounded pages contribute even when only 30 examples are shown.
        "active_project_counts": dict(sorted(counts.items())),
        "verifying_project_counts": dict(sorted(verifying_counts.items())),
        "verifying_count": sum(verifying_counts.values()),
        "status_source_windows": status_source_windows,
        "total_pages_read": sum(row["pages_read"] for row in status_source_windows),
        "items": fair_active_samples(rows),
        "items_sample_policy": "round_robin_source_order_by_work_status",
        "items_truncated": len(rows) > 30,
        "claim_scope_verified": False,
        "task_inventory_verified": False,
        "can_dispatch": False,
        "can_release_claim": False,
    }

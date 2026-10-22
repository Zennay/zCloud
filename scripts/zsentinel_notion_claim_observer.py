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
from datetime import date, datetime, timezone
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
    from zsentinel_product_issue_inbox import MAX_CONFIG_BYTES
    from zsentinel_recovery_router import read_json
    if (not isinstance(registry_path, Path) or not isinstance(canonical, dict)
            or not canonical or len(canonical) > 18):
        raise ValueError("canonical project registry unavailable")
    entries = read_json(registry_path, MAX_CONFIG_BYTES, "Notion project register")
    if not isinstance(entries, list):
        raise ValueError("invalid canonical project registry")
    # Only the single bounded inode above defines the Notion relation map.
    # Do not open the same path a second time: the source could be replaced
    # between snapshots with a different Notion mapping and unchanged IDs.
    result = {}
    seen_projects = set()
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("id") not in canonical:
            raise ValueError("unregistered Notion relation project")
        slug, url = entry["id"], entry.get("notion_url")
        if entry.get("repo_url") != "https://github.com/" + canonical[slug]:
            raise ValueError("Notion relation does not match canonical GitHub repository")
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



WORKERS = frozenset(("Worker 1", "Worker 2"))


def _worker_name(prop):
    """Expose the saved worker slot as metadata, never as a verified lease."""
    if not isinstance(prop, dict) or prop.get("type") != "select":
        raise ValueError("Notion Worker property is missing or incompatible")
    selected = prop.get("select")
    if selected is None:
        return None
    if not isinstance(selected, dict) or selected.get("name") not in WORKERS:
        raise ValueError("Notion Worker selection is unknown")
    return selected["name"]


def _date_start(prop, field):
    """Normalize a Notion date start while preserving unknown/empty values."""
    if not isinstance(prop, dict) or prop.get("type") != "date":
        raise ValueError("Notion " + field + " property is missing or incompatible")
    value = prop.get("date")
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("Notion " + field + " date is malformed")
    start = value.get("start")
    if type(start) is not str or not 1 <= len(start) <= 45:
        raise ValueError("Notion " + field + " start is invalid")
    if len(start) == 10:
        try:
            date.fromisoformat(start)
        except ValueError:
            raise ValueError("Notion " + field + " date is invalid") from None
        return start
    try:
        parsed = datetime.fromisoformat(start.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("Notion " + field + " timestamp is invalid") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Notion " + field + " timestamp lacks timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")



def classify_notional_claim_window(worker, claimed_at, claim_expires, observed_at):
    """Classify metadata consistency, never Notion/runtime ownership.

    A Running/Verifying status is not a lease. Even a bounded time window
    remains a Notion field observation, never a claim or dispatch grant.
    """
    if not worker or not claimed_at or not claim_expires:
        return "missing_worker_or_lease_fields"
    if len(claimed_at) == 10 or len(claim_expires) == 10:
        return "date_only_not_a_lease"
    try:
        start = datetime.fromisoformat(claimed_at.replace("Z", "+00:00"))
        expires = datetime.fromisoformat(claim_expires.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return "invalid_lease_timestamp"
    if (start.tzinfo is None or start.utcoffset() is None
            or expires.tzinfo is None or expires.utcoffset() is None):
        return "invalid_lease_timestamp"
    start = start.astimezone(timezone.utc)
    expires = expires.astimezone(timezone.utc)
    observed = observed_at.astimezone(timezone.utc)
    duration = (expires - start).total_seconds()
    if duration <= 0 or duration > 7 * 86400:
        return "invalid_lease_interval"
    if start > observed:
        return "future_claim_start"
    if expires <= observed:
        return "expired_claim_window"
    return "time_window_present_not_owner_verified"


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
                from zsentinel_product_issue_inbox import object_unique
                data = json.loads(
                    raw.decode("utf-8"), object_pairs_hook=object_unique,
                    parse_constant=lambda _: (_ for _ in ()).throw(
                        ValueError("nonstandard Notion JSON constant")))
            except (UnicodeError, json.JSONDecodeError, ValueError):
                # Duplicate Status/Project keys must not be resolved by
                # JSON's last-value-wins behavior: they can hide active work.
                raise ValueError("Notion response has ambiguous or invalid JSON") from None
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
                worker = _worker_name(props.get("Worker"))
                claimed_at = _date_start(props.get("Claimed At"), "Claimed At")
                claim_expires = _date_start(props.get("Claim Expires"), "Claim Expires")
                # Notion pages arrive sequentially. Do not evaluate identical
                # lease clocks against drifting row-by-row read times.
                rows.append({
                    "work": ("PWQ-" + str(number) if number else "Werk-ID onbekend"),
                    "title": title, "status": state,
                    "project": _project_name(props.get("Project"), known_projects),
                    "worker": worker,
                    "claimed_at": claimed_at,
                    "claim_expires": claim_expires,
                    "claim_metadata_status": (
                        "observed_fields_present" if worker and claimed_at and claim_expires
                        else "partial_or_missing"
                    ),
                    "claim_authorized": False,
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
    # Pin one post-read UTC clock for every lease classification and the
    # published source timestamp. A slow page series cannot make two rows
    # with the same expiry appear to have different claim-window states.
    observation_time = datetime.now(timezone.utc)
    for item in rows:
        item["claim_window_observation"] = classify_notional_claim_window(
            item["worker"], item["claimed_at"], item["claim_expires"],
            observation_time)
    # Histograms cover all read rows, not just visible sample rows.
    window_counts = {}
    project_windows = {}
    project_running_clock_problems = {}
    for item in rows:
        cls = item["claim_window_observation"]
        project = item["project"]
        window_counts[cls] = window_counts.get(cls, 0) + 1
        bucket = project_windows.setdefault(project, {})
        bucket[cls] = bucket.get(cls, 0) + 1
        if (item["status"] in ("Claimed", "Running")
                and cls != "time_window_present_not_owner_verified"):
            project_running_clock_problems[project] = (
                project_running_clock_problems.get(project, 0) + 1)
    counts, verifying_counts = {}, {}
    for row in rows:
        label = row["project"]
        counts[label] = counts.get(label, 0) + 1
        if row["status"] == "Verifying":
            verifying_counts[label] = verifying_counts.get(label, 0) + 1
    return {
        "source": "notion_readonly_data_source_pages",
        "observed_at": observation_time.isoformat().replace("+00:00", "Z"),
        "coverage": "incomplete" if incomplete else "current_pages_only",
        "active_count": len(rows),
        # Claimed/Running/Verifying are possible work-occupancy signals,
        # not proof of file-level claims or active worker leases.
        # All bounded pages contribute even when only 30 examples are shown.
        "active_project_counts": dict(sorted(counts.items())),
        "verifying_project_counts": dict(sorted(verifying_counts.items())),
        "verifying_count": sum(verifying_counts.values()),
        "claim_window_observation_counts": dict(sorted(window_counts.items())),
        "active_project_claim_window_counts": {
            project: dict(sorted(bucket.items()))
            for project, bucket in sorted(project_windows.items())
        },
        "active_project_in_progress_claim_clock_rechecks": {
            project: project_running_clock_problems.get(project, 0)
            for project in sorted(project_windows)
        },
        "claim_window_verified": False,
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

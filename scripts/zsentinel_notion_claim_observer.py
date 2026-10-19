#!/usr/bin/env python3
"""Bounded read-only Notion claim visibility for the private zSentinel cockpit.

The Notion data-source query is a read operation despite its POST transport.
Never infer file ownership, lease release, eligibility or worker recovery from
page properties. Token, page IDs and raw relation targets are never emitted.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone

UUID = re.compile(r"^(?:[0-9a-f]{32}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})$", re.I)
MAX_PAGES = 4
PAGE_SIZE = 100
MAX_BODY = 600_000
ACTIVE = frozenset(("Claimed", "Running"))


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


def observe_claims(data_source_id, token):
    if not isinstance(data_source_id, str) or not UUID.fullmatch(data_source_id):
        raise ValueError("Notion claim data source must be a canonical UUID")
    if not isinstance(token, str) or not token or len(token) > 512 or "\n" in token or "\r" in token:
        raise ValueError("Notion read token unavailable or invalid")
    endpoint = "https://api.notion.com/v1/data_sources/" + data_source_id + "/query"
    cursor = None
    seen_cursors = set()
    rows = []
    seen_pages = set()
    incomplete = False
    opener = urllib.request.build_opener(NoRedirect())
    for index in range(MAX_PAGES):
        payload = {"page_size": PAGE_SIZE}
        if cursor is not None:
            payload["start_cursor"] = cursor
        request = urllib.request.Request(endpoint,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={"Authorization": "Bearer " + token, "Notion-Version": "2025-09-03",
                     "Content-Type": "application/json", "Accept": "application/json"},
            method="POST")
        try:
            with opener.open(request, timeout=8) as response:
                if response.status != 200:
                    raise ValueError("Notion source unavailable")
                raw = response.read(MAX_BODY + 1)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            # Never forward provider exception strings: they can contain URLs
            # or request metadata, and token values must not enter reports.
            raise ValueError("Notion claim source unavailable") from None
        if len(raw) > MAX_BODY:
            raise ValueError("Notion claim source over size limit")
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            raise ValueError("Notion response is not valid UTF-8 JSON") from None
        if not isinstance(data, dict) or not isinstance(data.get("results"), list) or len(data["results"]) > PAGE_SIZE:
            raise ValueError("Invalid Notion claim page")
        for page in data["results"]:
            if not isinstance(page, dict) or not isinstance(page.get("properties"), dict):
                raise ValueError("Malformed Notion claim record")
            page_id = page.get("id")
            if (not isinstance(page_id, str) or not UUID.fullmatch(page_id)
                    or page_id.lower() in seen_pages):
                raise ValueError("Notion claim records must have distinct canonical page IDs")
            seen_pages.add(page_id.lower())
            props = page["properties"]
            status_prop = props.get("Status")
            if (not isinstance(status_prop, dict)
                    or status_prop.get("type") not in ("select", "status")):
                raise ValueError("Missing or incompatible Notion Status property")
            state = _state(status_prop)
            if not isinstance(state, str):
                raise ValueError("Invalid Notion work status type")
            if state not in ACTIVE:
                continue
            title = _text(props.get("Work item")) or "Onbenoemd actief werk"
            number = _work_number(props.get("Work ID"))
            rows.append({"work": ("PWQ-" + str(number) if number else "Werk-ID onbekend"),
                         "title": title, "status": state,
                         "project": "niet op padniveau geverifieerd"})
        more = data.get("has_more")
        if type(more) is not bool:
            raise ValueError("Invalid Notion pagination status")
        if not more:
            break
        cursor = data.get("next_cursor")
        if not isinstance(cursor, str) or not 1 <= len(cursor) <= 200 or cursor in seen_cursors:
            raise ValueError("Invalid Notion pagination cursor")
        seen_cursors.add(cursor)
        if index == MAX_PAGES - 1:
            incomplete = True
    rows.sort(key=lambda row: (row["status"], row["work"]))
    return {
        "source": "notion_readonly_data_source_pages",
        "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "coverage": "incomplete" if incomplete else "current_pages_only",
        "active_count": len(rows),
        "items": rows[:30],
        "items_truncated": len(rows) > 30,
        "claim_scope_verified": False,
        "task_inventory_verified": False,
        "can_dispatch": False,
        "can_release_claim": False,
    }

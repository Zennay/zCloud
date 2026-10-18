"""On-demand, canonical, bounded GitHub Activity and P0/P1 issue snapshots.

Two fixed read-only providers, two concurrent provider observations maximum,
cache-only receipt storage, and no tokens, queue edits or owner admission.
"""
from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
import time
from scripts import zcloud_github_activity_source as source
from scripts import zsentinel_product_issue_inbox as inbox

CACHE_SECONDS = 300
MAX_ENTRIES = 16
MAX_CONCURRENT = 2
_EXECUTOR = ThreadPoolExecutor(max_workers=MAX_CONCURRENT, thread_name_prefix="zcloud-gh-observe")
_LOCK = threading.Lock()
_ENTRIES: OrderedDict[tuple[str, str], dict] = OrderedDict()


def _envelope(project: str, kind: str, state: str) -> dict:
    return {
        "schema": ("zcloud-github-activity-live-v1" if kind == "activity"
                   else "zsentinel-product-issues-live-v1"),
        "state": state, "project": project, "receipt": None,
        "source": "canonical_github_readonly",
        "feed_complete": False,
        "authorizes_repair": False, "authorizes_merge": False,
        "authorizes_deploy": False, "retry_after_s": 3 if state == "refreshing" else 0,
        "notes": "Partial canonical PR/CI/main or issue observations. Claim and release not verified.",
    }


def _collect(registry: Path, project: str, kind: str) -> dict:
    # Explicit, public-only requests. Never read server GitHub credentials,
    # accept a provider URL, persist a secret or request GitHub write scopes.
    if kind == "activity":
        return source.collect(registry, [project], 48, 100,
                              token=None, previous=None)
    return inbox.collect(inbox.canonical_projects(registry), [project],
                         token=None, max_items=90)


def _valid_receipt(receipt: object, kind: str) -> bool:
    if not isinstance(receipt, dict):
        return False
    if kind == "activity":
        return (receipt.get("schema") == "zcloud-github-activity-readonly-v1"
                and receipt.get("feed_complete") is False
                and receipt.get("authorizes_repair") is False
                and receipt.get("authorizes_merge") is False
                and receipt.get("authorizes_deploy") is False
                and isinstance(receipt.get("coverage"), list)
                and isinstance(receipt.get("events"), list))
    return (receipt.get("schema") == "zsentinel-product-issue-inbox-v1"
            and receipt.get("actions_authorized") is False
            and receipt.get("queue_ownership_known") is False
            and receipt.get("active_claims_verified") is False
            and receipt.get("fully_synchronized") is False
            and isinstance(receipt.get("coverage"), list)
            and isinstance(receipt.get("items"), list))


def read_live(registry: Path, project: str, kind: str = "activity") -> dict:
    if kind not in ("activity", "issues"):
        raise ValueError("invalid_kind")
    if not isinstance(project, str) or not source.PROJECT_RE.fullmatch(project):
        raise ValueError("invalid_project")
    if project not in source.read_registry(registry):
        raise ValueError("unknown_project")
    now = time.monotonic()
    key = (kind, project)
    with _LOCK:
        entry = _ENTRIES.get(key)
        if entry is not None:
            _ENTRIES.move_to_end(key)
            future = entry.get("future")
            if future is not None:
                if not future.done():
                    return _envelope(project, kind, "refreshing")
                entry["future"] = None
                try:
                    data = future.result()
                except Exception:
                    # Never return arbitrary provider/URL/exception data.
                    entry.update(receipt=None, expires=now+30)
                    return _envelope(project, kind, "unavailable")
                if not _valid_receipt(data, kind):
                    entry.update(receipt=None, expires=now+30)
                    return _envelope(project, kind, "unavailable")
                entry.update(receipt=data, expires=now+CACHE_SECONDS)
            if now < entry.get("expires", 0):
                if entry.get("receipt") is not None:
                    result = _envelope(project, kind, "partial")
                    result["receipt"] = entry["receipt"]
                    result["retry_after_s"] = max(0, int(entry["expires"]-now))
                    return result
                return _envelope(project, kind, "unavailable")
        pending = sum(bool(item.get("future") is not None and
                           not item["future"].done()) for item in _ENTRIES.values())
        if pending >= MAX_CONCURRENT:
            return _envelope(project, kind, "capacity_wait")
        if key not in _ENTRIES and len(_ENTRIES) >= MAX_ENTRIES:
            oldest_finished = next((name for name, value in _ENTRIES.items()
                                    if value.get("future") is None
                                    or value["future"].done()), None)
            if oldest_finished is None:
                return _envelope(project, kind, "capacity_wait")
            _ENTRIES.pop(oldest_finished, None)
        _ENTRIES[key] = {"future": _EXECUTOR.submit(_collect, registry, project, kind),
                         "receipt": None, "expires": 0}
        _ENTRIES.move_to_end(key)
    return _envelope(project, kind, "refreshing")

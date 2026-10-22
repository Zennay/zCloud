"""On-demand, canonical, bounded GitHub Activity and P0/P1 issue snapshots.

Two fixed read-only providers, two concurrent provider observations maximum,
cache-only receipt storage, and no tokens, queue edits or owner admission.
"""
from __future__ import annotations

from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
import time
from scripts import zcloud_github_activity_source as source
from scripts import zsentinel_product_issue_inbox as inbox

CACHE_SECONDS = 300
MAX_ENTRIES = 16
MAX_CONCURRENT = 2
# Anonymous GitHub REST limit is shared by this VPS IP; leave margin for
# operator/other project traffic. Reserve worst-case provider calls at start.
MAX_PROVIDER_REQUESTS_PER_HOUR = 45
RATE_WINDOW_SECONDS = 3600
_RESERVATIONS = deque()
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
        "authorizes_deploy": False,
        "stale": False, "last_successful_sync_at": None, "stale_age_s": None,
        "retry_after_s": 3 if state == "refreshing" else 0,
        "notes": "Partial canonical PR/CI/main or issue observations. Claim and release not verified.",
    }


def _cached_envelope(project: str, kind: str, state: str,
                     entry: dict, now: float, retry_after_s: int = 0) -> dict:
    """Retain the last successful source receipt while refresh/retry is blocked."""
    result = _envelope(project, kind, state)
    receipt = entry.get("receipt")
    if receipt is not None:
        result["receipt"] = receipt
        result["last_successful_sync_at"] = (
            entry.get("last_successful_sync_at") or receipt.get("observed_at")
        )
        last_success = entry.get("last_success_monotonic")
        result["stale_age_s"] = (
            max(0, int(now - last_success))
            if isinstance(last_success, (int, float)) else None
        )
        result["stale"] = state != "partial" or bool(entry.get("stale"))
    result["retry_after_s"] = max(0, int(retry_after_s))
    return result

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
                    return _cached_envelope(
                        project, kind, "refreshing", entry, now, retry_after_s=3
                    )
                entry["future"] = None
                try:
                    data = future.result()
                except Exception:
                    # Keep the last successful bounded receipt; never expose
                    # provider exceptions, URLs, response bodies or credentials.
                    entry.update(expires=now + 30, stale=True)
                    if entry.get("receipt") is not None:
                        return _cached_envelope(
                            project, kind, "stale", entry, now, retry_after_s=30
                        )
                    return _envelope(project, kind, "unavailable")
                if not _valid_receipt(data, kind):
                    entry.update(expires=now + 30, stale=True)
                    if entry.get("receipt") is not None:
                        return _cached_envelope(
                            project, kind, "stale", entry, now, retry_after_s=30
                        )
                    return _envelope(project, kind, "unavailable")
                completed = time.monotonic()
                entry.update(
                    receipt=data,
                    expires=completed + CACHE_SECONDS,
                    stale=False,
                    last_successful_sync_at=source.stamp(source.utc_now()),
                    last_success_monotonic=completed,
                )
            if now < entry.get("expires", 0):
                if entry.get("receipt") is not None:
                    state = "stale" if entry.get("stale") else "partial"
                    return _cached_envelope(
                        project, kind, state, entry, now,
                        retry_after_s=max(0, int(entry["expires"] - now)),
                    )
                return _envelope(project, kind, "unavailable")
        pending = sum(bool(item.get("future") is not None and
                           not item["future"].done()) for item in _ENTRIES.values())
        if pending >= MAX_CONCURRENT:
            if entry is not None and entry.get("receipt") is not None:
                return _cached_envelope(
                    project, kind, "capacity_wait", entry, now, retry_after_s=3
                )
            return _envelope(project, kind, "capacity_wait")
        # Admission is intentionally conservative: every started background
        # request reserves the maximum REST calls for its collector. A failed
        # provider request keeps its reservation to prevent hammering 403/429.
        while _RESERVATIONS and now - _RESERVATIONS[0][0] >= RATE_WINDOW_SECONDS:
            _RESERVATIONS.popleft()
        reserved = sum(cost for _, cost in _RESERVATIONS)
        cost = 10 if kind == "activity" else 3
        if reserved + cost > MAX_PROVIDER_REQUESTS_PER_HOUR:
            retry_after = max(
                60, int(RATE_WINDOW_SECONDS - (now - _RESERVATIONS[0][0]))
            ) if _RESERVATIONS else 3600
            if entry is not None and entry.get("receipt") is not None:
                return _cached_envelope(
                    project, kind, "rate_budget_wait", entry, now,
                    retry_after_s=retry_after,
                )
            answer = _envelope(project, kind, "rate_budget_wait")
            answer["retry_after_s"] = retry_after
            return answer
        if key not in _ENTRIES and len(_ENTRIES) >= MAX_ENTRIES:
            oldest_finished = next((name for name, value in _ENTRIES.items()
                                    if value.get("future") is None
                                    or value["future"].done()), None)
            if oldest_finished is None:
                return _envelope(project, kind, "capacity_wait")
            _ENTRIES.pop(oldest_finished, None)
        _RESERVATIONS.append((now, cost))
        previous_receipt = entry.get("receipt") if entry is not None else None
        previous_success_at = entry.get("last_successful_sync_at") if entry is not None else None
        previous_success_mono = entry.get("last_success_monotonic") if entry is not None else None
        entry = {
            "future": _EXECUTOR.submit(_collect, registry, project, kind),
            "receipt": previous_receipt,
            "expires": 0,
            "stale": previous_receipt is not None,
            "last_successful_sync_at": previous_success_at,
            "last_success_monotonic": previous_success_mono,
        }
        _ENTRIES[key] = entry
        _ENTRIES.move_to_end(key)
    return _cached_envelope(project, kind, "refreshing", entry, time.monotonic(), retry_after_s=3)

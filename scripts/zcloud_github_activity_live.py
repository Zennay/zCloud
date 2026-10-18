"""Explicitly requested, bounded, non-mutating GitHub Activity on zCloud.

Public canonical repositories only by default. Network work is separated from
HTTP server request threads; requests return a partial/refreshing envelope and
must never create or modify a worker claim or persist a credential.
"""
from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
import time

from scripts import zcloud_github_activity_source as source

CACHE_SECONDS = 300
MAX_ENTRIES = 16
MAX_CONCURRENT = 2
_EXECUTOR = ThreadPoolExecutor(max_workers=MAX_CONCURRENT, thread_name_prefix="zcloud-gh-activity")
_LOCK = threading.Lock()
_ENTRIES: OrderedDict[str, dict] = OrderedDict()


def _unavailable(project: str, state: str) -> dict:
    return {
        "schema": "zcloud-github-activity-live-v1",
        "state": state,
        "project": project,
        "feed_complete": False,
        "authorizes_repair": False,
        "authorizes_merge": False,
        "authorizes_deploy": False,
        "source": "canonical_github_readonly",
        "receipt": None,
        "retry_after_s": 3 if state == "refreshing" else 0,
        "notes": "Public, bounded GitHub PR/CI/main observations. Branch/deploy/worker state unverified.",
    }


def _collect(registry: Path, project: str) -> dict:
    # No credential or GitHub write capability. Explicitly scoped to one
    # registered project and a bounded window. Private repositories fail closed.
    return source.collect(registry, [project], 48, 100, token=None, previous=None)


def read_live(registry: Path, project: str) -> dict:
    if not isinstance(project, str) or not source.PROJECT_RE.fullmatch(project):
        raise ValueError("invalid_project")
    # The only source of names and repositories is the canonical server registry,
    # not caller-provided URL/owner/path or request-supplied Authorization headers.
    if project not in source.read_registry(registry):
        raise ValueError("unknown_project")
    now = time.monotonic()
    with _LOCK:
        entry = _ENTRIES.get(project)
        if entry is not None:
            _ENTRIES.move_to_end(project)
            future = entry.get("future")
            if future is not None:
                if not future.done():
                    return _unavailable(project, "refreshing")
                entry["future"] = None
                try:
                    result = future.result()
                except Exception:
                    # No raw exception text, headers, token, private GitHub
                    # body or local path can escape the read-only endpoint.
                    entry["expires"] = now + 30
                    entry["receipt"] = None
                    return _unavailable(project, "unavailable")
                if (not isinstance(result, dict)
                    or result.get("feed_complete") is not False
                    or result.get("authorizes_merge") is not False
                    or result.get("authorizes_deploy") is not False
                    or result.get("authorizes_repair") is not False):
                    entry["expires"] = now + 30
                    entry["receipt"] = None
                    return _unavailable(project, "unavailable")
                entry["receipt"] = result
                entry["expires"] = now + CACHE_SECONDS
            if now < entry.get("expires", 0):
                if entry.get("receipt") is not None:
                    response = _unavailable(project, "partial")
                    response["receipt"] = entry["receipt"]
                    response["retry_after_s"] = max(0, int(entry["expires"] - now))
                    return response
                return _unavailable(project, "unavailable")
        pending = sum(1 for row in _ENTRIES.values()
                      if row.get("future") is not None and not row["future"].done())
        if pending >= MAX_CONCURRENT:
            return _unavailable(project, "capacity_wait")
        # Drop oldest finished item if a different project arrives; never create
        # unbounded futures or clear a live in-flight observation.
        if project not in _ENTRIES and len(_ENTRIES) >= MAX_ENTRIES:
            old = next((key for key, value in _ENTRIES.items()
                        if value.get("future") is None or value["future"].done()), None)
            if old is None:
                return _unavailable(project, "capacity_wait")
            _ENTRIES.pop(old, None)
        _ENTRIES[project] = {
            "future": _EXECUTOR.submit(_collect, registry, project),
            "receipt": None,
            "expires": 0,
        }
        _ENTRIES.move_to_end(project)
    return _unavailable(project, "refreshing")

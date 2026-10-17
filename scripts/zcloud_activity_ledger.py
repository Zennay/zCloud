"""Privacy-bounded retained GitHub activity view.

This never mutates the zCloud live database, control plane or project registry.
Only explicitly chosen local receipt files are written (mode 0600).
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
import tempfile

from scripts.zcloud_github_activity import ActivitySourceError, strict_json, stamp

MAX_LEDGER_BYTES = 2 * 1024 * 1024
MAX_LEDGER_EVENTS = 1600
MAX_VIEW_EVENTS = 480
KINDS = {
    "pr_created", "pr_updated", "pr_merged", "main_commit",
    "ci_run", "branch_tip_observed",
}


def _safe_record(item):
    if not isinstance(item, dict) or item.get("source") != "github":
        return False
    pid, repo, eid, kind = (item.get(key) for key in ("project", "repository", "id", "kind"))
    if not all(isinstance(v, str) for v in (pid, repo, eid, kind)):
        return False
    if not (0 < len(pid) < 42 and repo.startswith("Zennay/") and 0 < len(repo) <= 110):
        return False
    if kind not in KINDS or len(eid) > 260 or not eid.startswith("github:" + repo + ":" + kind + ":"):
        return False
    when = item.get("source_timestamp")
    if when is not None and stamp(when) is None:
        return False
    first = item.get("first_observed_at")
    if first is not None and stamp(first) is None:
        return False
    return True


def _fair(items, ids, limit):
    buckets = {pid: [] for pid in ids}
    for item in items:
        if item["project"] in buckets:
            buckets[item["project"]].append(item)
    for bucket in buckets.values():
        bucket.sort(key=lambda x: (x.get("source_timestamp") or x.get("first_observed_at") or "", x["id"]), reverse=True)
    queues = {pid: deque(buckets[pid]) for pid in ids}
    ordered = []
    while len(ordered) < limit and any(queues.values()):
        for pid in ids:
            if queues[pid] and len(ordered) < limit:
                ordered.append(queues[pid].popleft())
    return ordered


def _read(path):
    if not path.exists() and not path.is_symlink():
        return {"schema_version": 1, "events": [], "coverage": {}}
    try:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_LEDGER_BYTES:
            raise ActivitySourceError("ledger_not_regular_or_too_large")
        if (metadata.st_mode & 0o077) or metadata.st_uid != os.getuid():
            raise ActivitySourceError("ledger_permissions_not_private")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        try:
            opened = os.fstat(fd)
            if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
                raise ActivitySourceError("ledger_identity_changed")
            raw = os.read(fd, MAX_LEDGER_BYTES + 1)
        finally:
            os.close(fd)
        if len(raw) > MAX_LEDGER_BYTES:
            raise ActivitySourceError("ledger_too_large")
        data = strict_json(raw.decode("utf-8"))
    except (OSError, ValueError, UnicodeError):
        raise ActivitySourceError("ledger_cannot_be_read") from None
    if (not isinstance(data, dict) or data.get("schema_version") != 1 or
            not isinstance(data.get("coverage"), dict) or
            not isinstance(data.get("events"), list) or len(data["events"]) > MAX_LEDGER_EVENTS):
        raise ActivitySourceError("ledger_schema_invalid")
    if any(not _safe_record(item) for item in data["events"]):
        raise ActivitySourceError("ledger_record_invalid")
    return data


def _write(path, payload):
    # This path is explicitly supplied by a local operator; output never
    # lives in the served public directory unless they intentionally choose so.
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    encoded = (json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + "\n").encode("utf-8")
    if len(encoded) > MAX_LEDGER_BYTES:
        raise ActivitySourceError("ledger_bounded_size_exceeded")
    fd, temp = tempfile.mkstemp(prefix=".github-activity-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        # Refuse to replace a symlink introduced while the new receipt was made.
        if path.is_symlink():
            raise ActivitySourceError("ledger_symlink_refused")
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def merge_and_store(path, current):
    """Join fresh receipts to a private ledger, retaining last known events.

    Failure/partial coverage never gets promoted to synced, and old events
    stay visibly retained until a source confirms newer observations.
    """
    ledger = _read(Path(path))
    if current.get("schema_version") != 1 or not isinstance(current.get("coverage"), dict):
        raise ActivitySourceError("current_receipt_invalid")
    selected = list(current["coverage"])
    observed = stamp(current.get("observed_at"))
    if not observed:
        raise ActivitySourceError("current_observation_time_invalid")
    old = {item["id"]: item for item in ledger["events"] if _safe_record(item)}
    for item in current["events"]:
        if not _safe_record(item):
            raise ActivitySourceError("current_record_invalid")
        old[item["id"]] = item
    ids = list(dict.fromkeys([*ledger.get("coverage", {}), *selected]))
    # Explicit cap and fair ordering across projects to prevent starvation.
    all_events = _fair(old.values(), ids, MAX_LEDGER_EVENTS)
    coverage = dict(ledger["coverage"])
    for pid in selected:
        current_cov = dict(current["coverage"][pid])
        previous = coverage.get(pid, {})
        last_good = previous.get("last_successful_sync") if isinstance(previous, dict) else None
        if current_cov.get("last_successful_sync") is None and stamp(last_good):
            current_cov["last_successful_sync"] = last_good
        current_cov["retained_history"] = bool(any(x["project"] == pid for x in all_events))
        coverage[pid] = current_cov
    saved = {"schema_version": 1, "last_observed_at": observed,
             "coverage": coverage, "events": all_events}
    _write(Path(path), saved)
    view = dict(current)
    # Return only the explicitly selected projects even if the private ledger
    # retains other projects' history from a prior operator selection.
    view["events"] = _fair(all_events, selected, MAX_VIEW_EVENTS)
    view["events_truncated"] = len([x for x in all_events if x["project"] in selected]) > MAX_VIEW_EVENTS
    view["coverage"] = {pid: coverage[pid] for pid in selected}
    view["retained_history"] = True
    view["feed_complete"] = False
    return view

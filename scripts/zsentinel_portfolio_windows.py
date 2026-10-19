#!/usr/bin/env python3
"""Private, bounded round-robin GitHub source window cursor for zSentinel.

Only remembers which small source window was attempted. Never stores tokens,
claim ownership or job identifiers, and never authorizes a dispatch/retry.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

from zsentinel_recovery_router import read_json, write_json_atomic

SCHEMA = "zsentinel-portfolio-window-cursor-v1"
MAX_BYTES = 4096
VALID_COVERAGE = frozenset({
    "partial_both_sources", "ci_sample_only", "issue_sample_only",
    "degraded_or_denied", "not_requested_or_unavailable",
})


def acquire_window_lock(path):
    """Fail fast on concurrent scans sharing one private source cursor.

    An adjacent persistent lock inode, not the atomically replaced cursor
    inode, is locked for the entire operator invocation. Locking only the
    cursor file would fail after os.replace creates a new inode.
    """
    target = Path(path)
    parent = target.parent
    if not parent.is_dir() or parent.is_symlink():
        raise ValueError("window cursor directory must be an existing real directory")
    if not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("secure no-follow file opening is required")
    filename = str(target) + ".lock"
    fd = os.open(filename, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW |
                 getattr(os, "O_CLOEXEC", 0), 0o600)
    try:
        record = os.fstat(fd)
        if (not stat.S_ISREG(record.st_mode) or record.st_nlink != 1
                or record.st_uid != os.geteuid()
                or stat.S_IMODE(record.st_mode) & 0o077):
            raise ValueError("private window lock must be owned, regular, mode 0600")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("another operator owns this private scan window") from exc
        return os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise


def fingerprint(windows, registry):
    """Pin window identities, not only their count, across registry edits."""
    if not isinstance(registry, dict) or not isinstance(windows, list):
        raise ValueError("invalid window registry")
    serialized = json.dumps(
        {"windows": windows, "registry": registry},
        ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def select_next(windows, registry, path):
    """Read private cursor; registry changes explicitly reset coverage."""
    if not windows or any(not isinstance(w, list) or not w or len(w) > 3 for w in windows):
        raise ValueError("invalid bounded GitHub windows")
    identity = fingerprint(windows, registry)
    previous = read_json(path, MAX_BYTES, "window cursor") if path.exists() or path.is_symlink() else None
    if previous is None:
        return 0, None, "private_round_robin_initial"
    if (type(previous) is not dict or previous.get("schema") != SCHEMA
            or type(previous.get("next_window")) is not int
            or not 0 <= previous["next_window"] < 100
            or not isinstance(previous.get("registry_sha256"), str)
            or len(previous["registry_sha256"]) != 64
            or any(c not in "0123456789abcdef" for c in previous["registry_sha256"])):
        raise ValueError("invalid private window cursor; refusing silent rotation")
    last = previous.get("last_coverage")
    if (not isinstance(last, dict) or len(last) > 3
            or any(not isinstance(k, str) or len(k) > 48
                   or v not in VALID_COVERAGE for k, v in last.items())):
        raise ValueError("invalid cursor source coverage")
    # Registry changes invalidate every old sample, even if an old project
    # was removed. Do not accidentally keep a past project as current.
    if previous["registry_sha256"] != identity:
        return 0, previous, "registry_changed_start_new_cycle"
    _trusted_history(previous, registry)
    if previous["next_window"] >= len(windows):
        raise ValueError("window cursor index outside registry")
    return previous["next_window"], previous, "private_round_robin"


def _trusted_history(previous, registry):
    """Validate bounded historical attempts, never promote them to live health."""
    if previous is None:
        return {}
    source = previous.get("attempt_history", {})
    if not isinstance(source, dict) or len(source) > 32:
        raise ValueError("invalid private source history")
    now = datetime.now(timezone.utc)
    history = {}
    for pid, row in source.items():
        if (pid not in registry or not isinstance(row, dict)
                or row.get("coverage") not in VALID_COVERAGE
                or not isinstance(row.get("at_utc"), str)
                or len(row["at_utc"]) > 40):
            raise ValueError("invalid historic project receipt")
        try:
            stamp = datetime.fromisoformat(row["at_utc"])
        except ValueError as exc:
            raise ValueError("invalid historic source timestamp") from exc
        if (stamp.tzinfo is None or stamp.utcoffset() is None
                or stamp > now + timedelta(minutes=2)):
            raise ValueError("untrusted historic source timestamp")
        history[pid] = {"at_utc": stamp.astimezone(timezone.utc).isoformat(),
                        "coverage": row["coverage"]}
    return history


def coverage_history(windows, registry, previous, selected_index, coverage):
    """Show when sources were last *attempted*, not falsely last healthy."""
    if not isinstance(coverage, dict) or set(coverage) != set(windows[selected_index]):
        raise ValueError("missing source coverage for selected window")
    identity = fingerprint(windows, registry)
    history = (_trusted_history(previous, registry)
               if previous is not None and previous["registry_sha256"] == identity
               else {})
    now = datetime.now(timezone.utc).isoformat()
    for pid in windows[selected_index]:
        if coverage[pid] not in VALID_COVERAGE:
            raise ValueError("invalid source-coverage class")
        history[pid] = {"at_utc": now, "coverage": coverage[pid]}
    return history


def advance(path, windows, registry, previous, selected_index, coverage):
    """Advance only after validated output renders; reject racing invocations."""
    if not 0 <= selected_index < len(windows):
        raise ValueError("invalid selected window")
    if not isinstance(coverage, dict) or set(coverage) != set(windows[selected_index]):
        raise ValueError("missing selected source receipts")
    if any(value not in VALID_COVERAGE for value in coverage.values()):
        raise ValueError("invalid coverage for private rotation cursor")
    latest = read_json(path, MAX_BYTES, "window cursor") if path.exists() or path.is_symlink() else None
    if latest != previous:
        raise ValueError("private window cursor changed during scan; refusing lost update")
    history = coverage_history(windows, registry, previous, selected_index, coverage)
    next_state = {
        "schema": SCHEMA,
        "registry_sha256": fingerprint(windows, registry),
        "next_window": (selected_index + 1) % len(windows),
        "last_attempted_window": selected_index,
        "last_attempted_at_utc": datetime.now(timezone.utc).isoformat(),
        "last_coverage": {pid: coverage[pid] for pid in windows[selected_index]},
        "attempt_history": history,
        "claim_authorized": False,
        "actions_performed": False,
    }
    write_json_atomic(Path(path), next_state)
    return next_state

#!/usr/bin/env python3
"""Private, bounded round-robin GitHub source window cursor for zSentinel.

Only remembers which small source window was attempted. Never stores tokens,
claim ownership or job identifiers, and never authorizes a dispatch/retry.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from zsentinel_recovery_router import read_json, write_json_atomic

SCHEMA = "zsentinel-portfolio-window-cursor-v1"
MAX_BYTES = 4096
VALID_COVERAGE = frozenset({
    "partial_both_sources", "ci_sample_only", "issue_sample_only",
    "degraded_or_denied", "not_requested_or_unavailable",
})


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
            or any(k not in registry or v not in VALID_COVERAGE for k, v in last.items())):
        raise ValueError("invalid cursor source coverage")
    if previous["registry_sha256"] != identity:
        return 0, previous, "registry_changed_start_new_cycle"
    if previous["next_window"] >= len(windows):
        raise ValueError("window cursor index outside registry")
    return previous["next_window"], previous, "private_round_robin"


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
    next_state = {
        "schema": SCHEMA,
        "registry_sha256": fingerprint(windows, registry),
        "next_window": (selected_index + 1) % len(windows),
        "last_attempted_window": selected_index,
        "last_attempted_at_utc": datetime.now(timezone.utc).isoformat(),
        "last_coverage": {pid: coverage[pid] for pid in windows[selected_index]},
        "claim_authorized": False,
        "actions_performed": False,
    }
    write_json_atomic(Path(path), next_state)
    return next_state

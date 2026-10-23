#!/usr/bin/env python3
"""Read-only, source-exact reconciliation of two private zSentinel owner exports.

The JSON handoff is the authoritative source object. Reconstruct the existing
CSV exporter byte-for-byte; a matching digest alone cannot validate CSV rows.
No network, worker action, file creation, or authority escalation occurs.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import stat
from pathlib import Path

from zsentinel_runtime_handoff import SCHEMA, csv_export

MAX_JSON_BYTES = 1024 * 1024
MAX_CSV_BYTES = 512 * 1024
DIGEST = re.compile(r"^[a-f0-9]{64}$")


def _duplicate_guard(pairs):
    value = {}
    for name, item in pairs:
        if name in value:
            raise ValueError("duplicate key in owner handoff JSON")
        value[name] = item
    return value


def _nonfinite(value):
    raise ValueError("non-finite number in owner handoff JSON")


def _read_private_regular(path: Path, maximum: int):
    """Bound reads to one existing inode; refuse terminal, FIFO and symlink."""
    if not path.is_absolute() or path.is_symlink():
        raise ValueError("handoff input must be an absolute, non-symlink file")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("secure no-follow open is unavailable")
    flags |= os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
            raise ValueError("handoff input is not a bounded regular file")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(maximum + 1)
        if len(raw) > maximum:
            raise ValueError("handoff input exceeds size limit")
        return raw, (info.st_dev, info.st_ino)
    finally:
        os.close(fd)


def verify(json_path: Path, csv_path: Path):
    """Return source SHA and card counts, or refuse the whole pair."""
    json_raw, json_inode = _read_private_regular(json_path, MAX_JSON_BYTES)
    csv_raw, csv_inode = _read_private_regular(csv_path, MAX_CSV_BYTES)
    if json_inode == csv_inode:
        raise ValueError("JSON and CSV must be distinct existing files")
    report = json.loads(
        json_raw.decode("utf-8"), object_pairs_hook=_duplicate_guard,
        parse_constant=_nonfinite)
    if type(report) is not dict or report.get("schema") != SCHEMA:
        raise ValueError("invalid runtime owner JSON schema")
    observed = report.get("source_report_sha256")
    if type(observed) is not str or not DIGEST.fullmatch(observed):
        raise ValueError("missing canonical source-report digest")
    unwrapped = dict(report)
    del unwrapped["source_report_sha256"]
    canonical = json.dumps(
        unwrapped, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")
    calculated = hashlib.sha256(canonical).hexdigest()
    if not hmac.compare_digest(observed, calculated):
        raise ValueError("JSON content differs from its source-report digest")

    # This is stronger than comparing two self-declared digest strings:
    # original CSV contents, ordering, metadata and all action rows must
    # equal the existing producer's deterministic rendition of this JSON.
    expected_csv = csv_export(unwrapped).encode("utf-8")
    if not hmac.compare_digest(csv_raw, expected_csv):
        raise ValueError("CSV differs from the matching source JSON handoff")
    return {
        "source_report_sha256": calculated,
        "owner_action_cards": len(unwrapped["priority_items"]),
        "historical_rechecks": len(unwrapped.get("historical_owner_rechecks", [])),
        "merged_main_verified": False,
        "live_runtime_verified": False,
        "action_authorized": False,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Reconcile an existing private zSentinel JSON+CSV owner-handoff pair; no writes")
    parser.add_argument("--json", type=Path, required=True, help="existing absolute private JSON export")
    parser.add_argument("--csv", type=Path, required=True, help="existing absolute private CSV export")
    args = parser.parse_args()
    try:
        result = verify(args.json, args.csv)
    except (OSError, UnicodeError, ValueError, TypeError, RecursionError) as exc:
        parser.exit(2, f"zSentinel owner-export reconciliation REFUSED: {exc}\n")
    print(json.dumps({
        "status": "SOURCE_EXPORT_PAIR_MATCH",
        **result,
        "note": "Source consistency only; never proof of current claims, successful CI, merge or live recovery.",
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

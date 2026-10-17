"""Read-only, privacy-bounded pagination over retained GitHub activity.

For eventual eventstream integration, not a replacement for the protected
live /api/activity handler or authorized deployment.
"""
from __future__ import annotations

import argparse
import base64
from binascii import Error as Base64Error
import hashlib
import json
from pathlib import Path
import sys

from scripts.zcloud_activity_ledger import _fair, _read
from scripts.zcloud_github_activity import ActivitySourceError, strict_json

MAX_PAGE = 80
MAX_CURSOR_BYTES = 256


def _cursor_decode(token):
    if not isinstance(token, str) or len(token) > MAX_CURSOR_BYTES:
        raise ActivitySourceError("activity_cursor_invalid")
    try:
        encoded = token.encode("ascii")
        raw = base64.b64decode(encoded + b"=" * ((-len(encoded)) % 4), altchars=b"-_", validate=True)
        if len(raw) > 192:
            raise ValueError("overlong")
        value = strict_json(raw.decode('ascii'))
    except (UnicodeError, ValueError, Base64Error):
        raise ActivitySourceError("activity_cursor_invalid") from None
    if not isinstance(value, dict) or set(value) != {"v", "snapshot", "offset"}:
        raise ActivitySourceError("activity_cursor_invalid")
    if type(value["v"]) is not int or value["v"] != 1:
        raise ActivitySourceError("activity_cursor_invalid")
    if not isinstance(value["snapshot"], str) or len(value["snapshot"]) != 24:
        raise ActivitySourceError("activity_cursor_invalid")
    if type(value["offset"]) is not int or not 0 <= value["offset"] <= 1600:
        raise ActivitySourceError("activity_cursor_invalid")
    return value


def _cursor_encode(snapshot, offset):
    value = {"v": 1, "snapshot": snapshot, "offset": offset}
    raw = json.dumps(value, separators=(",", ":")).encode("ascii")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def page_activity(ledger, *, project=None, limit=80, cursor=None):
    """Return a stable page of source-only metadata with explicit coverage.

    The cursor binds to the current source-ledger snapshot. If that snapshot
    moves, the caller must restart from page 1; no stale offset is guessed.
    """
    if type(limit) is not int or not 1 <= limit <= MAX_PAGE:
        raise ActivitySourceError("activity_page_size_invalid")
    if not isinstance(ledger, dict) or ledger.get("schema_version") != 1:
        raise ActivitySourceError("activity_ledger_invalid")
    coverage = ledger.get("coverage")
    events = ledger.get("events")
    if not isinstance(coverage, dict) or not isinstance(events, list):
        raise ActivitySourceError("activity_ledger_invalid")
    known = sorted(coverage)
    if project is not None:
        if not isinstance(project, str) or project not in coverage:
            raise ActivitySourceError("activity_project_not_allowlisted")
        projects = [project]
    else:
        projects = known
    ordered = _fair(events, projects, len(events))
    # Bind the cursor to both the selection and exact ordered event identity,
    # not only a mutable offset. No private event text enters the digest.
    material = json.dumps([projects, [item["id"] for item in ordered]],
                          separators=(",", ":"), ensure_ascii=True)
    snapshot = hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
    offset = 0
    if cursor is not None:
        decoded = _cursor_decode(cursor)
        if decoded["snapshot"] != snapshot:
            raise ActivitySourceError("activity_cursor_stale_restart")
        offset = decoded["offset"]
        if offset >= len(ordered):
            raise ActivitySourceError("activity_cursor_exhausted")
    current = ordered[offset:offset + limit]
    next_offset = offset + len(current)
    # "live" means only the source's explicit source timestamps; reading a
    # private disk cache never proves GitHub is reachable at request time.
    return {
        "schema_version": 1,
        "source": "github",
        "feed_complete": False,
        "local_vps_events_synchronized": False,
        "source_snapshot": snapshot,
        "observed_at": ledger.get("last_observed_at"),
        "coverage": {pid: coverage[pid] for pid in projects},
        "events": current,
        "next_cursor": _cursor_encode(snapshot, next_offset) if next_offset < len(ordered) else None,
        "page_size": len(current),
        "retained_events": len(ordered),
        "source_status": "partial_or_retained",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only bounded page over private GitHub activity source ledger")
    parser.add_argument("--ledger", required=True, help="Private 0600 GitHub ledger source file")
    parser.add_argument("--project", help="Canonical project ID (default: all known in the ledger)")
    parser.add_argument("--limit", type=int, default=80, help="1..80 source events per page")
    parser.add_argument("--cursor", help="Opaque cursor from the previous exact ledger snapshot")
    args = parser.parse_args(argv)
    payload = page_activity(_read(Path(args.ledger)), project=args.project,
                            limit=args.limit, cursor=args.cursor)
    sys.stdout.write(json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ActivitySourceError as exc:
        sys.stderr.write("Private GitHub activity page unavailable: " + str(exc) + "\n")
        raise SystemExit(2)

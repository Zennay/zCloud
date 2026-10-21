#!/usr/bin/env python3
"""Versioned, privacy-bounded NEEDS_AI escalation packet contract.

This module validates the compact packet zCloud should attach when bounded
automatic retries are exhausted or a novel decision is required. It also audits
receipt coverage read-only without emitting tasks, logs, blockers, decisions,
queue IDs, or evidence payloads.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path
from typing import Any
from urllib.parse import quote

PACKET_KEY = "needs_ai_packet"
SCHEMA_VERSION = 1
MAX_PACKET_BYTES = 8_000
MAX_TEXT = 1_000
MAX_LOG_ITEMS = 8
MAX_LOG_ITEM = 500
MAX_EVIDENCE_BYTES = 4_000
MAX_EVIDENCE_KEYS = 20
REQUIRED_FIELDS = (
    "schema_version",
    "task",
    "last_good_step",
    "revision",
    "retries",
    "logs",
    "evidence",
    "blocker",
    "decision_needed",
)
_REQUIRED_RECEIPT_COLUMNS = {"id", "ci_status", "blocker", "evidence_json"}
_REVISION_RE = re.compile(r"^[A-Za-z0-9._/@:+-]{7,160}$")


def _require_regular_file(path: Path, label: str) -> Path:
    if path.is_symlink():
        raise ValueError(f"{label} path must not be a symlink")
    if not path.is_file():
        raise ValueError(f"{label} path must be an existing regular file")
    return path.resolve()


def _fingerprint(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns


def _connect_readonly(path: Path) -> sqlite3.Connection:
    resolved = _require_regular_file(path, "database")
    uri = f"file:{quote(str(resolved), safe='/')}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=10000")
    return connection


def _json_size(value: Any) -> int:
    return len(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _text_ok(value: Any, *, maximum: int = MAX_TEXT) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= maximum


def validate_needs_ai_packet(packet: Any) -> dict[str, Any]:
    """Validate shape and bounds without returning packet contents."""

    errors: list[str] = []
    if not isinstance(packet, dict):
        return {"valid": False, "error_codes": ["packet_not_object"]}

    try:
        packet_size = _json_size(packet)
    except (TypeError, ValueError):
        return {"valid": False, "error_codes": ["packet_not_json_serializable"]}

    if packet_size > MAX_PACKET_BYTES:
        errors.append("packet_too_large")

    missing = [field for field in REQUIRED_FIELDS if field not in packet]
    if missing:
        errors.append("missing_required_fields")

    if packet.get("schema_version") != SCHEMA_VERSION:
        errors.append("unsupported_schema_version")

    for field in ("task", "last_good_step", "blocker", "decision_needed"):
        if field in packet and not _text_ok(packet.get(field)):
            errors.append(f"invalid_{field}")

    revision = packet.get("revision")
    if revision is not None and (
        not isinstance(revision, str) or not _REVISION_RE.fullmatch(revision.strip())
    ):
        errors.append("invalid_revision")

    retries = packet.get("retries")
    if retries is not None and (
        isinstance(retries, bool)
        or not isinstance(retries, int)
        or retries < 0
        or retries > 100
    ):
        errors.append("invalid_retries")

    logs = packet.get("logs")
    if logs is not None:
        if not isinstance(logs, list) or not logs or len(logs) > MAX_LOG_ITEMS:
            errors.append("invalid_logs")
        elif any(not _text_ok(item, maximum=MAX_LOG_ITEM) for item in logs):
            errors.append("invalid_logs")

    evidence = packet.get("evidence")
    if evidence is not None:
        if not isinstance(evidence, dict):
            errors.append("invalid_evidence")
        else:
            if len(evidence) > MAX_EVIDENCE_KEYS:
                errors.append("evidence_too_many_keys")
            try:
                if _json_size(evidence) > MAX_EVIDENCE_BYTES:
                    errors.append("evidence_too_large")
            except (TypeError, ValueError):
                errors.append("invalid_evidence")

    return {
        "valid": not errors,
        "error_codes": sorted(set(errors)),
        "schema_version": SCHEMA_VERSION,
    }


def audit_needs_ai_coverage(
    db_path: str | Path,
    *,
    limit: int = 200,
) -> dict[str, Any]:
    """Audit recent failure/blocker receipts without exposing their payloads."""

    if limit < 1 or limit > 2_000:
        raise ValueError("limit must be between 1 and 2000")

    db = Path(db_path)
    resolved = _require_regular_file(db, "database")
    before = _fingerprint(resolved)

    failure_receipts = 0
    malformed_evidence_rows = 0
    packets_present = 0
    valid_packets = 0
    invalid_packets = 0
    missing_packets = 0
    validation_errors: Counter[str] = Counter()

    with closing(_connect_readonly(db)) as connection:
        tables = {
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "project_state_receipts" not in tables:
            raise ValueError("missing required project_state_receipts table")

        columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(project_state_receipts)"
            )
        }
        missing_columns = sorted(_REQUIRED_RECEIPT_COLUMNS - columns)
        if missing_columns:
            raise ValueError(
                "project_state_receipts missing required columns: "
                + ",".join(missing_columns)
            )

        rows = connection.execute(
            """
            SELECT ci_status,blocker,evidence_json
            FROM project_state_receipts
            WHERE lower(ci_status)='failure' OR trim(blocker)<>''
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        for row in rows:
            failure_receipts += 1
            raw = row["evidence_json"] or "{}"
            try:
                evidence = json.loads(raw)
            except (TypeError, ValueError, json.JSONDecodeError):
                malformed_evidence_rows += 1
                missing_packets += 1
                continue
            if not isinstance(evidence, dict):
                malformed_evidence_rows += 1
                missing_packets += 1
                continue

            packet = evidence.get(PACKET_KEY)
            if packet is None:
                missing_packets += 1
                continue

            packets_present += 1
            result = validate_needs_ai_packet(packet)
            if result["valid"]:
                valid_packets += 1
            else:
                invalid_packets += 1
                validation_errors.update(result["error_codes"])

    after = _fingerprint(resolved)

    if failure_receipts == 0:
        coverage_state = "no_failure_receipts"
    elif packets_present == 0:
        coverage_state = "no_packets"
    elif valid_packets == failure_receipts:
        coverage_state = "complete"
    else:
        coverage_state = "partial"

    return {
        "schema_version": SCHEMA_VERSION,
        "receipt_limit": int(limit),
        "failure_receipts_observed": int(failure_receipts),
        "packets_present": int(packets_present),
        "valid_packets": int(valid_packets),
        "invalid_packets": int(invalid_packets),
        "missing_packets": int(missing_packets),
        "malformed_evidence_rows": int(malformed_evidence_rows),
        "validation_error_counts": dict(sorted(validation_errors.items())),
        "coverage_state": coverage_state,
        "coverage_complete": bool(
            failure_receipts > 0
            and valid_packets == failure_receipts
            and invalid_packets == 0
            and malformed_evidence_rows == 0
        ),
        "database_fingerprint_unchanged": before == after,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()

    report = audit_needs_ai_coverage(args.database, limit=args.limit)
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))

    if not report["database_fingerprint_unchanged"]:
        return 3
    if args.require_complete and not report["coverage_complete"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Read-only coverage audit for zCloud iteration hypotheses stored in receipts."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1
REQUIRED_FIELDS = ("problem", "change", "expected_effect", "validation")
REQUIRED_COLUMNS = {"id", "project_id", "source", "evidence_json"}


class HypothesisAuditError(RuntimeError):
    pass


def _open_readonly(db_path: Path) -> sqlite3.Connection:
    target = db_path.expanduser()
    if target.is_symlink():
        raise HypothesisAuditError("refusing symlink database path")
    if not target.is_file():
        raise HypothesisAuditError("database path is not a regular file")
    connection = sqlite3.connect(f"file:{target.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _validate_schema(connection: sqlite3.Connection) -> None:
    columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(project_state_receipts)")
    }
    missing = sorted(REQUIRED_COLUMNS - columns)
    if missing:
        raise HypothesisAuditError(
            "project_state_receipts missing required columns: " + ",".join(missing)
        )


def _classify_evidence(raw: str) -> tuple[str, tuple[str, ...]]:
    try:
        evidence = json.loads(raw or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return "malformed_evidence", REQUIRED_FIELDS
    if not isinstance(evidence, dict):
        return "malformed_evidence", REQUIRED_FIELDS

    hypothesis = evidence.get("iteration_hypothesis")
    if hypothesis is None:
        return "missing_contract", REQUIRED_FIELDS
    if not isinstance(hypothesis, dict):
        return "invalid_contract", REQUIRED_FIELDS
    if hypothesis.get("schema_version") != SCHEMA_VERSION:
        return "invalid_contract", REQUIRED_FIELDS

    missing = tuple(
        field
        for field in REQUIRED_FIELDS
        if not isinstance(hypothesis.get(field), str)
        or not hypothesis[field].strip()
    )
    if missing:
        return "incomplete_contract", missing
    return "complete", ()


def audit(
    db_path: Path,
    *,
    project_id: str = "cloud",
    limit: int = 50,
) -> dict:
    project_id = str(project_id or "").strip()
    if not project_id:
        raise HypothesisAuditError("project_id is required")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 500:
        raise HypothesisAuditError("limit must be between 1 and 500")

    before = db_path.lstat() if db_path.exists() or db_path.is_symlink() else None
    connection = _open_readonly(db_path)
    try:
        _validate_schema(connection)
        initial_changes = connection.total_changes
        rows = connection.execute(
            """SELECT evidence_json
               FROM project_state_receipts
               WHERE project_id=?
                 AND source LIKE 'portfolio_queue:%'
               ORDER BY id DESC
               LIMIT ?""",
            (project_id, limit),
        ).fetchall()
        if connection.total_changes != initial_changes:
            raise HypothesisAuditError("read-only audit unexpectedly changed SQLite state")
    finally:
        connection.close()

    after = db_path.lstat()
    immutable = bool(
        before
        and before.st_size == after.st_size
        and before.st_mtime_ns == after.st_mtime_ns
    )
    if not immutable:
        raise HypothesisAuditError("database file changed during read-only audit")

    counts = {
        "complete": 0,
        "missing_contract": 0,
        "invalid_contract": 0,
        "incomplete_contract": 0,
        "malformed_evidence": 0,
    }
    missing_fields = {field: 0 for field in REQUIRED_FIELDS}
    for row in rows:
        status, missing = _classify_evidence(row["evidence_json"])
        counts[status] += 1
        for field in missing:
            missing_fields[field] += 1

    total = len(rows)
    complete = counts["complete"]
    if total == 0:
        coverage_status = "no_receipts"
    elif complete == total:
        coverage_status = "complete"
    else:
        coverage_status = "incomplete"

    return {
        "project_id": project_id,
        "contract": {
            "name": "iteration_hypothesis",
            "schema_version": SCHEMA_VERSION,
            "required_fields": list(REQUIRED_FIELDS),
        },
        "scope": {
            "receipt_source": "portfolio_queue:*",
            "limit": limit,
            "receipts_observed": total,
        },
        "coverage": {
            "status": coverage_status,
            "complete": complete,
            "incomplete": total - complete,
            "counts": counts,
            "missing_fields": missing_fields,
        },
        "database_immutable": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit zCloud iteration-hypothesis receipt coverage without mutation"
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("/home/ubuntu/zennay-cloud/history.db"),
    )
    parser.add_argument("--project", default="cloud")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--require-complete",
        action="store_true",
        help="exit non-zero unless every observed queue receipt has hypothesis v1",
    )
    args = parser.parse_args(argv)

    try:
        result = audit(args.db, project_id=args.project, limit=args.limit)
    except (HypothesisAuditError, sqlite3.Error) as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        else:
            print("ZCLOUD_ITERATION_HYPOTHESIS_AUDIT_ERROR", str(exc))
        return 2

    if args.json:
        print(json.dumps({"ok": True, **result}, sort_keys=True))
    else:
        coverage = result["coverage"]
        print(
            "ZCLOUD_ITERATION_HYPOTHESIS_AUDIT",
            f"status={coverage['status']}",
            f"observed={result['scope']['receipts_observed']}",
            f"complete={coverage['complete']}",
        )

    if args.require_complete and result["coverage"]["status"] != "complete":
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

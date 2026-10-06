#!/usr/bin/env python3
"""Read-only audit for durable queue-to-runner correlation coverage.

This deliberately does not infer correlation from timestamps, worker slots, titles, or
other heuristics. A layer is linked only when it carries the exact durable queue token.
The report is aggregate-only: queue IDs, claim keys, command payloads, event payloads,
and receipt evidence are never emitted.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import quote

REQUIRED_TABLES = {
    "portfolio_queue",
    "task_claims",
    "runner_commands",
    "runner_events",
    "project_state_receipts",
}
TOKEN_COLUMNS = ("correlation_id", "queue_id")


def _fingerprint(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns


def _connect_readonly(path: Path) -> sqlite3.Connection:
    if path.is_symlink():
        raise ValueError("database path must not be a symlink")
    if not path.is_file():
        raise ValueError("database path must be an existing regular file")
    resolved = path.resolve()
    uri = f"file:{quote(str(resolved), safe='/')}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=10000")
    return connection


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table})")}


def _safe_object(raw: Any) -> dict[str, Any] | None:
    if raw in (None, ""):
        return {}
    try:
        value = json.loads(str(raw))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _queue_id_from_object(value: dict[str, Any]) -> str:
    direct = str(value.get("queue_id") or value.get("portfolio_queue_id") or "").strip()
    if direct:
        return direct
    nested = value.get("queue_item")
    if isinstance(nested, dict):
        return str(nested.get("queue_id") or "").strip()
    return ""


def _receipt_queue_id(source: str, evidence: dict[str, Any] | None) -> str:
    prefix = "portfolio_queue:"
    if source.startswith(prefix):
        return source[len(prefix):].strip()
    return _queue_id_from_object(evidence or {})


def _linked_values(
    connection: sqlite3.Connection,
    table: str,
    column: str | None,
    known_queue_ids: set[str],
) -> int:
    if not column:
        return 0
    linked = 0
    for row in connection.execute(
        f'SELECT "{column}" AS token FROM "{table}" WHERE "{column}" IS NOT NULL'
    ):
        token = str(row["token"] or "").strip()
        if token and token in known_queue_ids:
            linked += 1
    return linked


def audit_correlation_coverage(db_path: str | Path) -> dict[str, Any]:
    path = Path(db_path)
    before = _fingerprint(path)

    with _connect_readonly(path) as connection:
        tables = {
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        missing_tables = sorted(REQUIRED_TABLES - tables)
        if missing_tables:
            raise ValueError(
                "missing required correlation tables: " + ",".join(missing_tables)
            )

        queue_columns = _columns(connection, "portfolio_queue")
        command_columns = _columns(connection, "runner_commands")
        event_columns = _columns(connection, "runner_events")

        queue_rows = list(
            connection.execute(
                "SELECT queue_id,status FROM portfolio_queue ORDER BY queue_id"
            )
        )
        known_queue_ids = {
            str(row["queue_id"] or "").strip()
            for row in queue_rows
            if str(row["queue_id"] or "").strip()
        }
        status_counts = Counter(str(row["status"] or "unknown") for row in queue_rows)

        malformed_claim_metadata = 0
        claim_queue_ids: set[str] = set()
        for row in connection.execute("SELECT metadata_json FROM task_claims"):
            metadata = _safe_object(row["metadata_json"])
            if metadata is None:
                malformed_claim_metadata += 1
                continue
            queue_id = _queue_id_from_object(metadata)
            if queue_id:
                claim_queue_ids.add(queue_id)

        malformed_receipt_evidence = 0
        receipt_queue_ids: set[str] = set()
        for row in connection.execute(
            "SELECT source,evidence_json FROM project_state_receipts"
        ):
            evidence = _safe_object(row["evidence_json"])
            if evidence is None:
                malformed_receipt_evidence += 1
                evidence = {}
            queue_id = _receipt_queue_id(str(row["source"] or ""), evidence)
            if queue_id:
                receipt_queue_ids.add(queue_id)

        shared_token_column = next(
            (
                candidate
                for candidate in TOKEN_COLUMNS
                if candidate in queue_columns
                and candidate in command_columns
                and candidate in event_columns
            ),
            None,
        )

        command_token_column = next(
            (candidate for candidate in TOKEN_COLUMNS if candidate in command_columns),
            None,
        )
        event_token_column = next(
            (candidate for candidate in TOKEN_COLUMNS if candidate in event_columns),
            None,
        )

        queue_to_claim_linked = len(known_queue_ids & claim_queue_ids)
        queue_to_receipt_linked = len(known_queue_ids & receipt_queue_ids)
        command_linked_rows = _linked_values(
            connection, "runner_commands", command_token_column, known_queue_ids
        )
        event_linked_rows = _linked_values(
            connection, "runner_events", event_token_column, known_queue_ids
        )

    after = _fingerprint(path)
    if before != after:
        raise RuntimeError("read-only correlation audit changed database size or mtime")

    reason_codes: list[str] = []
    if shared_token_column is None:
        reason_codes.append("no_common_durable_runner_token")
    if command_token_column is None:
        reason_codes.append("runner_commands_missing_queue_or_correlation_id")
    if event_token_column is None:
        reason_codes.append("runner_events_missing_queue_or_correlation_id")
    if known_queue_ids and queue_to_claim_linked == 0:
        reason_codes.append("no_exact_queue_to_claim_links")
    if known_queue_ids and queue_to_receipt_linked == 0:
        reason_codes.append("no_exact_queue_to_receipt_links")
    if malformed_claim_metadata:
        reason_codes.append("malformed_claim_metadata")
    if malformed_receipt_evidence:
        reason_codes.append("malformed_receipt_evidence")
    if not known_queue_ids:
        reason_codes.append("no_queue_items_to_measure")

    end_to_end_ready = bool(
        known_queue_ids
        and shared_token_column
        and command_linked_rows > 0
        and event_linked_rows > 0
    )

    return {
        "schema_version": 1,
        "read_only": True,
        "queue_items_examined": len(known_queue_ids),
        "queue_status_counts": dict(sorted(status_counts.items())),
        "links": {
            "queue_to_claim": {
                "linked_items": queue_to_claim_linked,
                "missing_items": max(0, len(known_queue_ids) - queue_to_claim_linked),
            },
            "queue_to_receipt": {
                "linked_items": queue_to_receipt_linked,
                "missing_items": max(0, len(known_queue_ids) - queue_to_receipt_linked),
            },
            "runner_commands": {
                "token_column": command_token_column,
                "linked_rows": command_linked_rows,
            },
            "runner_events": {
                "token_column": event_token_column,
                "linked_rows": event_linked_rows,
            },
        },
        "common_runner_token_column": shared_token_column,
        "end_to_end_ready": end_to_end_ready,
        "reason_codes": sorted(set(reason_codes)),
        "database_fingerprint_unchanged": before == after,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit durable zCloud queue-to-runner correlation coverage read-only."
    )
    parser.add_argument("--db", required=True, help="Path to zCloud history.db")
    parser.add_argument(
        "--require-ready",
        action="store_true",
        help="Exit non-zero when durable end-to-end correlation is not ready.",
    )
    args = parser.parse_args()

    report = audit_correlation_coverage(args.db)
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    if args.require_ready and not report["end_to_end_ready"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

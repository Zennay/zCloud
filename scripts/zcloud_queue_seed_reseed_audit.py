#!/usr/bin/env python3
"""Audit whether queue seed rows could reappear after runtime-row cleanup."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SEED = ROOT / "portfolio_queue.seed.json"
SCHEMA_VERSION = 1
MAX_SEED_BYTES = 2 * 1024 * 1024
QUEUE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
PROJECT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
RUNNABLE_SEED_STATUSES = frozenset({"queued"})


class QueueSeedAuditError(ValueError):
    """Raised when seed/runtime evidence cannot be audited safely."""


def _queue_id(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) > 160 or not QUEUE_ID_RE.fullmatch(text):
        raise QueueSeedAuditError(f"invalid queue id: {text!r}")
    return text


def _project_id(value: Any) -> str:
    text = str(value or "").strip().lower()
    if len(text) > 32 or not PROJECT_ID_RE.fullmatch(text):
        raise QueueSeedAuditError(f"invalid project id: {text!r}")
    return text


def load_seed(path: Path) -> list[dict[str, Any]]:
    path = Path(path)
    if path.is_symlink():
        raise QueueSeedAuditError("queue seed must be a regular non-symlink file")
    if not path.exists() or not path.is_file():
        raise QueueSeedAuditError("queue seed is missing or not a regular file")
    if path.stat().st_size > MAX_SEED_BYTES:
        raise QueueSeedAuditError("queue seed exceeds 2 MiB")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise QueueSeedAuditError("queue seed is unreadable JSON") from exc
    if not isinstance(raw, list):
        raise QueueSeedAuditError("queue seed must be a JSON list")
    return raw


def load_runtime_rows(db_path: Path) -> dict[str, dict[str, Any]]:
    db_path = Path(db_path)
    if not db_path.exists() or not db_path.is_file():
        raise QueueSeedAuditError("runtime database is missing or not a regular file")

    uri = db_path.resolve().as_uri() + "?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='portfolio_queue'"
        ).fetchone()
        if not table:
            raise QueueSeedAuditError("runtime database has no portfolio_queue table")
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(portfolio_queue)").fetchall()
        }
        required = {"queue_id", "project_id", "status", "eligible"}
        if not required.issubset(columns):
            raise QueueSeedAuditError("portfolio_queue schema is missing required columns")
        rows = connection.execute(
            "SELECT queue_id,project_id,status,eligible FROM portfolio_queue"
        ).fetchall()
    except sqlite3.Error as exc:
        raise QueueSeedAuditError("runtime database cannot be read safely") from exc
    finally:
        try:
            connection.close()
        except (UnboundLocalError, sqlite3.Error):
            pass

    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        queue_id = str(row["queue_id"] or "").strip()
        if not queue_id:
            continue
        result[queue_id] = {
            "project_id": str(row["project_id"] or "").strip().lower(),
            "status": str(row["status"] or "").strip().lower(),
            "eligible": bool(row["eligible"]),
        }
    return result


def build_report(
    seed: list[dict[str, Any]],
    runtime_rows: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for raw in seed:
        if not isinstance(raw, dict):
            raise QueueSeedAuditError("queue seed rows must be JSON objects")
        queue_id = _queue_id(raw.get("queue_id"))
        if queue_id in seen:
            raise QueueSeedAuditError(f"duplicate queue seed id: {queue_id}")
        seen.add(queue_id)

        project_id = _project_id(raw.get("project_id"))
        seed_status = str(raw.get("status") or "queued").strip().lower()
        seed_eligible = bool(raw.get("eligible", True))
        runtime = runtime_rows.get(queue_id)

        if runtime is None:
            would_reseed_runnable = seed_eligible and seed_status in RUNNABLE_SEED_STATUSES
            classification = (
                "absent_reseed_risk" if would_reseed_runnable else "absent_non_runnable"
            )
            runtime_status = None
            runtime_eligible = None
        else:
            would_reseed_runnable = False
            classification = "present_runtime_row"
            runtime_status = str(runtime.get("status") or "").strip().lower() or None
            runtime_eligible = bool(runtime.get("eligible"))

        items.append(
            {
                "queue_id": queue_id,
                "project_id": project_id,
                "title": str(raw.get("title") or queue_id).strip()[:160],
                "seed_status": seed_status,
                "seed_eligible": seed_eligible,
                "runtime_present": runtime is not None,
                "runtime_status": runtime_status,
                "runtime_eligible": runtime_eligible,
                "would_reseed_runnable": would_reseed_runnable,
                "classification": classification,
            }
        )

    risks = [item["queue_id"] for item in items if item["would_reseed_runnable"]]
    return {
        "schema_version": SCHEMA_VERSION,
        "seed_item_count": len(items),
        "runtime_present_count": sum(1 for item in items if item["runtime_present"]),
        "absent_count": sum(1 for item in items if not item["runtime_present"]),
        "runnable_reseed_risk_count": len(risks),
        "runnable_reseed_risk_ids": risks,
        "safe": not risks,
        "items": items,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare portfolio_queue.seed.json with the live SQLite queue without writes."
    )
    parser.add_argument("--seed", type=Path, default=DEFAULT_SEED)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument(
        "--require-safe",
        action="store_true",
        help="Exit 3 when an absent seed row would be reinserted runnable on next initialization.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        report = build_report(load_seed(args.seed), load_runtime_rows(args.db))
    except QueueSeedAuditError as exc:
        print(
            json.dumps(
                {"schema_version": SCHEMA_VERSION, "status": "invalid", "error": str(exc)},
                sort_keys=True,
            )
        )
        return 2

    print(json.dumps(report, sort_keys=True))
    if args.require_safe and not report["safe"]:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

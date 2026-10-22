#!/usr/bin/env python3
"""Bounded retry/backoff contract and read-only zCloud readiness audit."""

from __future__ import annotations

import argparse
import ast
import json
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path
from typing import Any
from urllib.parse import quote

POLICY_SCHEMA_VERSION = 1
FAILURE_CLASSES = ("transient", "novel", "terminal")
DISPOSITIONS = ("FAILED_RETRYABLE", "NEEDS_AI", "FAILED_FINAL")
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BASE_DELAY_SECONDS = 30
DEFAULT_MAX_DELAY_SECONDS = 900
_REQUIRED_QUEUE_COLUMNS = {"status", "attempts"}


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


def retry_disposition(
    failure_class: str,
    attempts: Any,
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    base_delay_seconds: int = DEFAULT_BASE_DELAY_SECONDS,
    max_delay_seconds: int = DEFAULT_MAX_DELAY_SECONDS,
) -> dict[str, Any]:
    """Return one deterministic retry/escalation disposition.

    attempts is the number of attempts already consumed by the failing job.
    A retry is allowed only while attempts < max_attempts.
    """

    failure_class = str(failure_class or "").strip().lower()
    if failure_class not in FAILURE_CLASSES:
        raise ValueError("unsupported failure class")
    if isinstance(attempts, bool):
        raise ValueError("attempts must be a non-negative integer")
    try:
        attempt_count = int(attempts)
    except (TypeError, ValueError):
        raise ValueError("attempts must be a non-negative integer")
    if attempt_count < 0:
        raise ValueError("attempts must be a non-negative integer")
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")
    if base_delay_seconds < 1 or max_delay_seconds < base_delay_seconds:
        raise ValueError("invalid backoff bounds")

    if failure_class == "novel":
        return {
            "schema_version": POLICY_SCHEMA_VERSION,
            "disposition": "NEEDS_AI",
            "retry": False,
            "delay_seconds": 0,
            "reason_code": "novel_failure_requires_reasoning",
        }
    if failure_class == "terminal":
        return {
            "schema_version": POLICY_SCHEMA_VERSION,
            "disposition": "FAILED_FINAL",
            "retry": False,
            "delay_seconds": 0,
            "reason_code": "explicit_terminal_failure",
        }
    if attempt_count >= max_attempts:
        return {
            "schema_version": POLICY_SCHEMA_VERSION,
            "disposition": "NEEDS_AI",
            "retry": False,
            "delay_seconds": 0,
            "reason_code": "retry_budget_exhausted",
        }

    exponent = max(0, attempt_count - 1)
    delay = min(max_delay_seconds, base_delay_seconds * (2 ** exponent))
    return {
        "schema_version": POLICY_SCHEMA_VERSION,
        "disposition": "FAILED_RETRYABLE",
        "retry": True,
        "delay_seconds": int(delay),
        "reason_code": "known_transient_with_budget",
    }


def _source_retry_contract(source_path: Path) -> dict[str, Any]:
    source = _require_regular_file(source_path, "source").read_text(encoding="utf-8")
    if len(source.encode("utf-8")) > 4 * 1024 * 1024:
        raise ValueError("source file is unexpectedly large")
    tree = ast.parse(source, filename=str(source_path))

    finish_node = next(
        (
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "portfolio_queue_finish"
        ),
        None,
    )
    finish_segment = ast.get_source_segment(source, finish_node) if finish_node else ""

    accepted = sorted(
        result
        for result in ("FAILED_RETRYABLE", "NEEDS_AI", "FAILED_FINAL")
        if result in (finish_segment or "")
    )
    attempt_increment_present = "attempts=attempts+1" in source
    native_backoff_marker_present = any(
        marker in source
        for marker in (
            "retry_after",
            "next_retry_at",
            "retry_not_before",
            "backoff_seconds",
        )
    )
    failure_class_marker_present = any(
        marker in source for marker in ("failure_class", "retry_class")
    )

    gap_codes: list[str] = []
    if not attempt_increment_present:
        gap_codes.append("attempt_counter_increment_missing")
    if "FAILED_RETRYABLE" not in accepted:
        gap_codes.append("failed_retryable_transition_missing")
    if "NEEDS_AI" not in accepted:
        gap_codes.append("needs_ai_transition_missing")
    if "FAILED_FINAL" not in accepted:
        gap_codes.append("failed_final_transition_missing")
    if not native_backoff_marker_present:
        gap_codes.append("retry_backoff_marker_missing")
    if not failure_class_marker_present:
        gap_codes.append("failure_classification_marker_missing")

    return {
        "attempt_counter_increment_present": attempt_increment_present,
        "accepted_retry_results": accepted,
        "native_backoff_marker_present": native_backoff_marker_present,
        "failure_classification_marker_present": failure_class_marker_present,
        "writer_retry_contract_complete": not gap_codes,
        "gap_codes": gap_codes,
    }


def audit_retry_readiness(
    db_path: str | Path,
    source_path: str | Path,
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> dict[str, Any]:
    db = Path(db_path)
    source = Path(source_path)
    resolved = _require_regular_file(db, "database")
    before = _fingerprint(resolved)

    attempt_counts: Counter[str] = Counter()
    active_rows = 0
    exhausted_active_rows = 0
    max_attempts_observed = 0

    with closing(_connect_readonly(db)) as connection:
        tables = {
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "portfolio_queue" not in tables:
            raise ValueError("missing required portfolio_queue table")
        columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(portfolio_queue)")
        }
        missing = sorted(_REQUIRED_QUEUE_COLUMNS - columns)
        if missing:
            raise ValueError(
                "portfolio_queue missing required columns: " + ",".join(missing)
            )

        for row in connection.execute("SELECT status,attempts FROM portfolio_queue"):
            status = str(row["status"] or "").strip().lower()
            try:
                attempts = max(0, int(row["attempts"] or 0))
            except (TypeError, ValueError):
                attempts = 0
            max_attempts_observed = max(max_attempts_observed, attempts)
            bucket = (
                "0" if attempts == 0
                else "1" if attempts == 1
                else "2" if attempts == 2
                else "3+"
            )
            attempt_counts[bucket] += 1
            if status in {"queued", "claimed", "running", "verifying"}:
                active_rows += 1
                if attempts >= max_attempts:
                    exhausted_active_rows += 1

    after = _fingerprint(resolved)
    source_contract = _source_retry_contract(source)

    return {
        "schema_version": POLICY_SCHEMA_VERSION,
        "max_attempts_policy": int(max_attempts),
        "rows_observed": int(sum(attempt_counts.values())),
        "attempt_buckets": {
            key: int(attempt_counts.get(key, 0)) for key in ("0", "1", "2", "3+")
        },
        "max_attempts_observed": int(max_attempts_observed),
        "active_rows": int(active_rows),
        "active_rows_at_or_over_retry_budget": int(exhausted_active_rows),
        "source_contract": source_contract,
        "writer_retry_contract_complete": bool(
            source_contract["writer_retry_contract_complete"]
        ),
        "database_fingerprint_unchanged": before == after,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    parser.add_argument(
        "--source",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "server.py",
    )
    parser.add_argument("--max-attempts", type=int, default=DEFAULT_MAX_ATTEMPTS)
    parser.add_argument("--require-writer", action="store_true")
    args = parser.parse_args()

    report = audit_retry_readiness(
        args.database,
        args.source,
        max_attempts=args.max_attempts,
    )
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    if not report["database_fingerprint_unchanged"]:
        return 3
    if args.require_writer and not report["writer_retry_contract_complete"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

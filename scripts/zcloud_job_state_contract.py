#!/usr/bin/env python3
"""Read-only contract audit for zCloud's canonical project-job states.

The portfolio queue currently carries legacy storage states. This module gives the
control plane one bounded canonical vocabulary without mutating production state,
and proves exactly which native transitions are still missing before writer
integration is attempted.

No queue IDs, titles, blockers, evidence payloads, command payloads, or other
free-form production text are emitted.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path
from typing import Any
from urllib.parse import quote

CANONICAL_STATES = (
    "QUEUED",
    "RUNNING",
    "DONE",
    "BLOCKED",
    "NEEDS_AI",
    "FAILED_RETRYABLE",
    "FAILED_FINAL",
)

_NATIVE_STATUS_MAP = {
    "queued": "QUEUED",
    "running": "RUNNING",
    "done": "DONE",
    "blocked": "BLOCKED",
    "needs_ai": "NEEDS_AI",
    "failed_retryable": "FAILED_RETRYABLE",
    "failed_final": "FAILED_FINAL",
}

_LEGACY_STATUS_MAP = {
    "claimed": "RUNNING",
    "verifying": "RUNNING",
    "dropped": "BLOCKED",
}

_RECOGNIZED_RAW_STATUSES = frozenset(
    set(_NATIVE_STATUS_MAP) | set(_LEGACY_STATUS_MAP) | {"failed"}
)

_REQUIRED_QUEUE_COLUMNS = {"status", "blocker", "attempts"}


def _fingerprint(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns


def _require_regular_file(path: Path, label: str) -> Path:
    if path.is_symlink():
        raise ValueError(f"{label} path must not be a symlink")
    if not path.is_file():
        raise ValueError(f"{label} path must be an existing regular file")
    return path.resolve()


def _connect_readonly(path: Path) -> sqlite3.Connection:
    resolved = _require_regular_file(path, "database")
    uri = f"file:{quote(str(resolved), safe='/')}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=10000")
    return connection


def canonical_job_state(
    raw_status: Any,
    *,
    attempts: Any = 0,
    retry_limit: int = 3,
) -> str:
    """Map a bounded storage state to the canonical control-plane vocabulary."""

    status = str(raw_status or "").strip().lower()
    if status in _NATIVE_STATUS_MAP:
        return _NATIVE_STATUS_MAP[status]
    if status in _LEGACY_STATUS_MAP:
        return _LEGACY_STATUS_MAP[status]
    if status == "failed":
        try:
            attempt_count = max(0, int(attempts or 0))
        except (TypeError, ValueError):
            raise ValueError("failed queue state requires an integer attempts value")
        return "FAILED_RETRYABLE" if attempt_count < retry_limit else "FAILED_FINAL"
    raise ValueError("unrecognized portfolio queue state")


def _portfolio_queue_finish_contract(source_path: Path) -> dict[str, Any]:
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
    if finish_node is None:
        raise ValueError("portfolio_queue_finish function not found")

    segment = ast.get_source_segment(source, finish_node) or ""
    accepted = sorted(
        {
            match
            for match in re.findall(
                r"['\"](DONE|BLOCKED|CONTINUE|NEEDS_AI|FAILED_RETRYABLE|FAILED_FINAL)['\"]",
                segment,
            )
        }
    )
    stored = sorted(
        {
            status
            for status in re.findall(r"status=['\"]([a-z_]+)['\"]", segment)
            if status in _RECOGNIZED_RAW_STATUSES
        }
    )

    gaps: list[str] = []
    if "BLOCKED" in accepted and "blocked" not in stored:
        gaps.append("blocked_collapsed_to_legacy_storage")
    if "NEEDS_AI" not in accepted and "needs_ai" not in stored:
        gaps.append("needs_ai_transition_missing")
    if "FAILED_RETRYABLE" not in accepted and "failed_retryable" not in stored:
        gaps.append("failed_retryable_transition_missing")
    if "FAILED_FINAL" not in accepted and "failed_final" not in stored:
        gaps.append("failed_final_transition_missing")

    return {
        "accepted_results": accepted,
        "stored_states": stored,
        "native_terminal_contract_complete": not gaps,
        "gap_codes": gaps,
    }


def audit_job_state_contract(
    db_path: str | Path,
    source_path: str | Path,
    *,
    retry_limit: int = 3,
) -> dict[str, Any]:
    db = Path(db_path)
    source = Path(source_path)
    before = _fingerprint(_require_regular_file(db, "database"))

    canonical_counts: Counter[str] = Counter()
    recognized_counts: Counter[str] = Counter()
    legacy_rows = 0
    unknown_rows = 0
    dropped_without_blocker = 0

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
        missing_columns = sorted(_REQUIRED_QUEUE_COLUMNS - columns)
        if missing_columns:
            raise ValueError(
                "portfolio_queue missing required columns: " + ",".join(missing_columns)
            )

        for row in connection.execute(
            "SELECT status, blocker, attempts FROM portfolio_queue"
        ):
            raw_status = str(row["status"] or "").strip().lower()
            if raw_status in _RECOGNIZED_RAW_STATUSES:
                recognized_counts[raw_status] += 1
            try:
                canonical = canonical_job_state(
                    raw_status,
                    attempts=row["attempts"],
                    retry_limit=retry_limit,
                )
            except ValueError:
                unknown_rows += 1
                continue
            canonical_counts[canonical] += 1
            if raw_status in _LEGACY_STATUS_MAP or raw_status == "failed":
                legacy_rows += 1
            if raw_status == "dropped" and not str(row["blocker"] or "").strip():
                dropped_without_blocker += 1

    after = _fingerprint(db)
    source_contract = _portfolio_queue_finish_contract(source)

    mapped_states = sorted(state for state, count in canonical_counts.items() if count)
    missing_observed_states = [
        state for state in CANONICAL_STATES if canonical_counts.get(state, 0) == 0
    ]

    return {
        "schema_version": 1,
        "canonical_states": list(CANONICAL_STATES),
        "rows_observed": int(sum(canonical_counts.values()) + unknown_rows),
        "recognized_status_counts": dict(sorted(recognized_counts.items())),
        "canonical_state_counts": {
            state: int(canonical_counts.get(state, 0)) for state in CANONICAL_STATES
        },
        "mapped_states_observed": mapped_states,
        "states_not_observed": missing_observed_states,
        "legacy_mapped_rows": int(legacy_rows),
        "unknown_status_rows": int(unknown_rows),
        "dropped_without_blocker_rows": int(dropped_without_blocker),
        "source_contract": source_contract,
        "mapping_complete": unknown_rows == 0,
        "native_lifecycle_complete": bool(
            unknown_rows == 0 and source_contract["native_terminal_contract_complete"]
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
    parser.add_argument("--retry-limit", type=int, default=3)
    parser.add_argument("--require-native", action="store_true")
    args = parser.parse_args()

    if args.retry_limit < 1:
        parser.error("--retry-limit must be >= 1")

    report = audit_job_state_contract(
        args.database,
        args.source,
        retry_limit=args.retry_limit,
    )
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))

    if not report["database_fingerprint_unchanged"]:
        return 3
    if args.require_native and not report["native_lifecycle_complete"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

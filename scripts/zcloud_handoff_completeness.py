#!/usr/bin/env python3
"""Audit whether latest zCloud handoff receipts contain the required handoff facts.

This is a strictly read-only completeness contract. It reports only project ids,
receipt ids and missing-field reason codes; raw handoff/evidence content is
never emitted.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = "handoff-completeness-v1"
MAX_PROJECTS = 100
MAX_PROJECTS_BYTES = 2 * 1024 * 1024
MAX_EVIDENCE_BYTES = 12_000
PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
COMMIT_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")
PR_URL_RE = re.compile(r"/pull/(\d+)(?:$|[/?#])")
ALLOWED_STATUS = {
    "queued",
    "in_progress",
    "success",
    "failure",
    "cancelled",
    "skipped",
}
PR_KEYS = {
    "pr",
    "pr_number",
    "pull_request",
    "pull_request_number",
    "github_pr",
    "github_pr_number",
}


def _bounded_file(path: Path, label: str) -> None:
    if path.is_symlink():
        raise ValueError(f"{label} path must not be a symlink")
    if not path.is_file():
        raise ValueError(f"{label} file is missing")
    if path.stat().st_size > MAX_PROJECTS_BYTES and label == "projects":
        raise ValueError("projects file exceeds bounded size")


def _active_projects(path: Path) -> list[str]:
    _bounded_file(path, "projects")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("projects file is not valid UTF-8 JSON") from exc
    if not isinstance(payload, list):
        raise ValueError("projects catalog must be a list")
    active: list[str] = []
    seen: set[str] = set()
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("project catalog entries must be objects")
        if str(item.get("status") or "").strip().lower() != "active":
            continue
        project_id = str(item.get("id") or "").strip()
        if not PROJECT_RE.fullmatch(project_id):
            raise ValueError("active project id is not canonical")
        if project_id in seen:
            raise ValueError("duplicate active project id")
        seen.add(project_id)
        active.append(project_id)
    if not active:
        raise ValueError("projects catalog has no active projects")
    if len(active) > MAX_PROJECTS:
        raise ValueError(f"active project count exceeds {MAX_PROJECTS}")
    return sorted(active)


def _open_ro(path: Path) -> sqlite3.Connection:
    _bounded_file(path, "database")
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _columns(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(project_state_receipts)")
    }


def _require_schema(connection: sqlite3.Connection) -> None:
    required = {
        "id",
        "project_id",
        "action",
        "commit_sha",
        "ci_status",
        "next_gate",
        "observed_at",
        "evidence_json",
    }
    actual = _columns(connection)
    if not actual:
        raise RuntimeError("project_state_receipts table is missing")
    missing = required - actual
    if missing:
        raise RuntimeError(
            "project_state_receipts schema missing: " + ",".join(sorted(missing))
        )


def _parse_observed_at(value: object, now: datetime) -> str | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    parsed = parsed.astimezone(timezone.utc)
    if parsed > now:
        return None
    return parsed.isoformat()


def _has_evidence_value(value: object, depth: int = 0) -> bool:
    if depth > 4:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value != 0
    if isinstance(value, list):
        return any(_has_evidence_value(item, depth + 1) for item in value[:100])
    if isinstance(value, dict):
        return any(_has_evidence_value(item, depth + 1) for item in list(value.values())[:100])
    return False


def _parse_evidence(raw: object) -> dict | None:
    text = str(raw or "{}")
    if len(text.encode("utf-8")) > MAX_EVIDENCE_BYTES:
        return None
    try:
        value = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _valid_pr_value(value: object) -> bool:
    if isinstance(value, int) and not isinstance(value, bool):
        return value > 0
    text = str(value or "").strip()
    if text.isdigit():
        return int(text) > 0
    return bool(PR_URL_RE.search(text))


def _evidence_has_pr(value: object, depth: int = 0) -> bool:
    if depth > 4:
        return False
    if isinstance(value, dict):
        for key, item in list(value.items())[:100]:
            if str(key).strip().lower() in PR_KEYS and _valid_pr_value(item):
                return True
            if _evidence_has_pr(item, depth + 1):
                return True
    elif isinstance(value, list):
        return any(_evidence_has_pr(item, depth + 1) for item in value[:100])
    return False


def _latest_rows(connection: sqlite3.Connection, project_ids: list[str]) -> dict[str, sqlite3.Row]:
    placeholders = ",".join("?" for _ in project_ids)
    rows = connection.execute(
        f"""SELECT r.id,r.project_id,r.action,r.commit_sha,r.ci_status,
                   r.next_gate,r.observed_at,r.evidence_json
            FROM project_state_receipts r
            JOIN (
              SELECT project_id,MAX(id) AS id
              FROM project_state_receipts
              WHERE project_id IN ({placeholders})
              GROUP BY project_id
            ) latest ON latest.id=r.id""",
        project_ids,
    ).fetchall()
    return {str(row["project_id"]): row for row in rows}


def _assess(row: sqlite3.Row | None, now: datetime) -> dict:
    if row is None:
        return {
            "state": "missing",
            "receipt_id": None,
            "observed_at": None,
            "missing": ["receipt"],
        }

    missing: list[str] = []
    evidence = _parse_evidence(row["evidence_json"])
    observed = _parse_observed_at(row["observed_at"], now)

    if not str(row["action"] or "").strip():
        missing.append("change_summary")
    if evidence is None:
        missing.append("evidence_valid")
    elif not _has_evidence_value(evidence):
        missing.append("proof")
    commit_ok = bool(COMMIT_RE.fullmatch(str(row["commit_sha"] or "").strip()))
    pr_ok = bool(evidence is not None and _evidence_has_pr(evidence))
    if not (commit_ok or pr_ok):
        missing.append("commit_or_pr")
    if str(row["ci_status"] or "").strip().lower() not in ALLOWED_STATUS:
        missing.append("live_status")
    if not str(row["next_gate"] or "").strip():
        missing.append("next_safe_task")
    if observed is None:
        missing.append("observed_at_valid")

    state = "complete" if not missing else ("invalid" if "evidence_valid" in missing or "observed_at_valid" in missing else "incomplete")
    return {
        "state": state,
        "receipt_id": int(row["id"]),
        "observed_at": observed,
        "missing": missing,
    }


def audit(
    db: Path,
    projects: Path,
    *,
    now: datetime | None = None,
) -> dict:
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        raise ValueError("reference time must be timezone-aware")
    reference = reference.astimezone(timezone.utc)
    project_ids = _active_projects(projects)

    with closing(_open_ro(db)) as connection:
        _require_schema(connection)
        rows = _latest_rows(connection, project_ids)
        if connection.total_changes != 0:
            raise RuntimeError("read-only handoff audit changed SQLite state")

    results = []
    for project_id in project_ids:
        result = {"project_id": project_id, **_assess(rows.get(project_id), reference)}
        results.append(result)

    counts = Counter(item["state"] for item in results)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": reference.isoformat(),
        "ready": all(item["state"] == "complete" for item in results),
        "project_count": len(results),
        "counts": {
            "complete": counts.get("complete", 0),
            "incomplete": counts.get("incomplete", 0),
            "invalid": counts.get("invalid", 0),
            "missing": counts.get("missing", 0),
        },
        "required_facts": [
            "change_summary",
            "proof",
            "commit_or_pr",
            "live_status",
            "next_safe_task",
        ],
        "privacy_contract": "reason-codes-only",
        "read_only": True,
        "projects": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit zCloud handoff receipt completeness")
    root = Path(__file__).resolve().parents[1]
    parser.add_argument("--db", type=Path, default=root / "history.db")
    parser.add_argument("--projects", type=Path, default=root / "projects.json")
    parser.add_argument("--require-ready", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    payload = audit(args.db, args.projects)
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
    return 0 if (payload["ready"] or not args.require_ready) else 3


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Deterministic read-only planner for post-gate zCloud PR integration batches.

Consumes the body-free backlog report from zcloud_serialized_gate_backlog_audit.py
plus complete changed-file evidence for every current-main candidate. It selects a
small file-disjoint batch deterministically. It never authorizes or performs merges.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

SCHEMA = "zcloud-serialized-gate-batch-plan-v1"
BACKLOG_SCHEMA = "zcloud-serialized-gate-backlog-audit-v1"
MAX_INPUT_BYTES = 8 * 1024 * 1024
MAX_CANDIDATES = 200
MAX_FILES_PER_PR = 500
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class PlanError(ValueError):
    pass


def _load(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise PlanError("input symlink rejected")
    try:
        stat = path.stat()
    except OSError as exc:
        raise PlanError(f"input unavailable: {exc}") from exc
    if stat.st_size > MAX_INPUT_BYTES:
        raise PlanError("input exceeds bounded size")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PlanError(f"invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise PlanError("input must be a JSON object")
    return payload


def _safe_path(value: Any) -> str:
    path = str(value or "")
    if not path or len(path) > 500:
        raise PlanError("invalid changed path")
    if path.startswith("/") or path.startswith("\\"):
        raise PlanError("absolute changed path rejected")
    parts = path.replace("\\", "/").split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise PlanError("unsafe changed path")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in path):
        raise PlanError("control character in changed path")
    return path


def _number(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise PlanError("invalid PR number") from exc
    if number < 1:
        raise PlanError("invalid PR number")
    return number


def _validate_backlog(backlog: Any) -> dict[str, Any]:
    if not isinstance(backlog, dict):
        raise PlanError("backlog report missing")
    if backlog.get("schema") != BACKLOG_SCHEMA:
        raise PlanError("unexpected backlog schema")
    if backlog.get("inventory_complete") is not True:
        raise PlanError("backlog inventory incomplete")
    if backlog.get("mutation_performed") is not False:
        raise PlanError("backlog evidence is not read-only")
    sha = str(backlog.get("current_main_sha") or "").strip().lower()
    if not SHA_RE.fullmatch(sha):
        raise PlanError("invalid current main SHA")
    gate_open = backlog.get("gate_open")
    integration_released = backlog.get("integration_released")
    if not isinstance(gate_open, bool) or not isinstance(integration_released, bool):
        raise PlanError("invalid gate release state")
    if integration_released != (not gate_open):
        raise PlanError("incoherent gate release state")
    gates = backlog.get("gates")
    if not isinstance(gates, list) or len(gates) != 2:
        raise PlanError("expected exactly two gate records")
    gate_numbers = {int(item.get("number") or 0) for item in gates if isinstance(item, dict)}
    if gate_numbers != {576, 580}:
        raise PlanError("unexpected serialized gate identities")
    dependents = backlog.get("dependents")
    if not isinstance(dependents, list):
        raise PlanError("dependent PR evidence missing")
    if backlog.get("dependent_pr_count") != len(dependents):
        raise PlanError("dependent PR count mismatch")
    expected_ready = sum(1 for item in dependents if isinstance(item, dict) and item.get("review_ready_current_main") is True)
    expected_drafts = sum(1 for item in dependents if isinstance(item, dict) and item.get("draft") is True)
    expected_stale = sum(1 for item in dependents if isinstance(item, dict) and item.get("base_is_current_main") is False)
    if backlog.get("review_ready_current_main_count") != expected_ready:
        raise PlanError("review-ready count mismatch")
    if backlog.get("draft_count") != expected_drafts:
        raise PlanError("draft count mismatch")
    if backlog.get("stale_base_count") != expected_stale:
        raise PlanError("stale-base count mismatch")
    return backlog


def build_plan(payload: dict[str, Any], *, batch_limit: int = 10) -> dict[str, Any]:
    if batch_limit < 1 or batch_limit > 50:
        raise PlanError("batch limit must be 1..50")
    backlog = _validate_backlog(payload.get("backlog"))
    files_by_pr = payload.get("files_by_pr")
    if not isinstance(files_by_pr, dict):
        raise PlanError("files_by_pr must be an object")

    candidate_rows: list[dict[str, Any]] = []
    seen: set[int] = set()
    for raw in backlog["dependents"]:
        if not isinstance(raw, dict):
            raise PlanError("invalid dependent PR evidence")
        number = _number(raw.get("number"))
        if number in seen:
            raise PlanError(f"duplicate dependent PR #{number}")
        seen.add(number)
        if raw.get("review_ready_current_main") is not True:
            continue
        if raw.get("draft") is not False or raw.get("base_is_current_main") is not True:
            raise PlanError(f"incoherent current-main candidate #{number}")

        entry = files_by_pr.get(str(number))
        if not isinstance(entry, dict):
            raise PlanError(f"missing changed-file evidence for PR #{number}")
        if entry.get("complete") is not True:
            raise PlanError(f"incomplete changed-file evidence for PR #{number}")
        files = entry.get("files")
        if not isinstance(files, list) or not files:
            raise PlanError(f"invalid changed-file evidence for PR #{number}")
        if len(files) > MAX_FILES_PER_PR:
            raise PlanError(f"changed-file evidence exceeds limit for PR #{number}")
        normalized = [_safe_path(path) for path in files]
        if len(normalized) != len(set(normalized)):
            raise PlanError(f"duplicate changed path for PR #{number}")
        candidate_rows.append({"number": number, "files": tuple(sorted(normalized))})

    if len(candidate_rows) > MAX_CANDIDATES:
        raise PlanError("candidate inventory exceeds bounded limit")
    expected_file_keys = {str(row["number"]) for row in candidate_rows}
    if set(files_by_pr) != expected_file_keys:
        raise PlanError("changed-file evidence key set mismatch")

    candidate_rows.sort(key=lambda row: (len(row["files"]), row["number"]))
    selected: list[dict[str, Any]] = []
    path_owner: dict[str, int] = {}
    deferred: list[dict[str, Any]] = []

    for row in candidate_rows:
        conflicts = sorted({path_owner[path] for path in row["files"] if path in path_owner})
        if conflicts or len(selected) >= batch_limit:
            deferred.append(
                {
                    "number": row["number"],
                    "reason": "file_conflict" if conflicts else "batch_limit",
                    "conflicts_with": conflicts,
                }
            )
            continue
        selected.append({"number": row["number"], "file_count": len(row["files"])})
        for path in row["files"]:
            path_owner[path] = row["number"]

    gate_released = backlog["integration_released"] is True
    if not candidate_rows:
        status = "empty"
    elif not gate_released:
        status = "blocked_preview"
    else:
        status = "candidate_batch"

    return {
        "schema": SCHEMA,
        "current_main_sha": backlog["current_main_sha"],
        "status": status,
        "gate_released": gate_released,
        "preview_only": not gate_released,
        "candidate_count": len(candidate_rows),
        "batch_limit": batch_limit,
        "selected_count": len(selected),
        "selected": selected,
        "deferred_count": len(deferred),
        "deferred": deferred,
        "ci_revalidation_required": True,
        "ownership_revalidation_required": True,
        "merge_authorized": False,
        "mutation_performed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Plan a deterministic file-disjoint zCloud post-gate PR batch"
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--batch-limit", type=int, default=10)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        payload = _load(args.input)
        result = build_plan(payload, batch_limit=args.batch_limit)
    except PlanError as exc:
        if args.json:
            print(
                json.dumps(
                    {
                        "schema": SCHEMA,
                        "status": "invalid",
                        "error": str(exc),
                        "merge_authorized": False,
                        "mutation_performed": False,
                    },
                    sort_keys=True,
                )
            )
        else:
            print(f"SERIALIZED_GATE_BATCH_INVALID: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, sort_keys=True, indent=2))
    else:
        print(
            "SERIALIZED_GATE_BATCH "
            f"status={result['status']} "
            f"candidates={result['candidate_count']} "
            f"selected={result['selected_count']} "
            f"deferred={result['deferred_count']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

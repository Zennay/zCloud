#!/usr/bin/env python3
"""Read-only audit for stale serialized-gate references in open PR metadata.

A serialized gate owner can be superseded without changing every already-open PR
body that names the old owner. This audit consumes a bounded GitHub snapshot and
reports only PR numbers whose coordination text still treats a retired gate as
current. It never mutates GitHub, runtime, queue, browser, service, or SQLite state.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

SCHEMA = "zcloud-serialized-gate-metadata-drift-audit-v1"
MAX_INPUT_BYTES = 4 * 1024 * 1024
MAX_OPEN_PULLS = 500
MAX_BODY_CHARS = 100_000
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SERIALIZED_MARKERS = (
    "serialized",
    "keep unmerged",
    "integration window",
    "live writer",
    "production/control-plane window",
    "control-plane window",
)


class AuditError(ValueError):
    pass


def _sha(value: Any, field: str) -> str:
    text = str(value or "").strip().lower()
    if not SHA_RE.fullmatch(text):
        raise AuditError(f"invalid {field}")
    return text


def _number(value: Any, field: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise AuditError(f"invalid {field}") from exc
    if number < 1:
        raise AuditError(f"invalid {field}")
    return number


def _bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise AuditError(f"invalid {field}")
    return value


def _state(value: Any, field: str) -> str:
    state = str(value or "").strip().lower()
    if state not in {"open", "closed"}:
        raise AuditError(f"invalid {field}")
    return state


def _body(value: Any) -> str:
    text = str(value or "")
    if len(text) > MAX_BODY_CHARS:
        raise AuditError("PR body exceeds bounded input size")
    return text


def _load_json(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise AuditError("snapshot symlink rejected")
    try:
        stat = path.stat()
    except OSError as exc:
        raise AuditError(f"snapshot unavailable: {exc}") from exc
    if stat.st_size > MAX_INPUT_BYTES:
        raise AuditError("snapshot exceeds bounded input size")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AuditError(f"invalid snapshot JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise AuditError("snapshot must be a JSON object")
    return payload


def _serialized_context(body: str) -> bool:
    lowered = body.lower()
    return any(marker in lowered for marker in SERIALIZED_MARKERS)


def audit_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    repository = str(payload.get("repository") or "").strip()
    if not repository or len(repository) > 200 or "/" not in repository:
        raise AuditError("invalid repository")
    current_main_sha = _sha(payload.get("current_main_sha"), "current_main_sha")
    if not _bool(payload.get("inventory_complete"), "inventory_complete"):
        raise AuditError("open PR inventory incomplete")

    succession = payload.get("gate_succession")
    if not isinstance(succession, dict):
        raise AuditError("gate_succession must be an object")
    primary_gate = _number(succession.get("primary_gate"), "primary_gate")
    retired_gate = _number(succession.get("retired_gate"), "retired_gate")
    successor_gate = _number(succession.get("successor_gate"), "successor_gate")
    if len({primary_gate, retired_gate, successor_gate}) != 3:
        raise AuditError("gate identities must be distinct")

    retired_state = _state(succession.get("retired_state"), "retired_state")
    retired_merged = _bool(succession.get("retired_merged"), "retired_merged")
    successor_state = _state(succession.get("successor_state"), "successor_state")
    successor_merged = _bool(succession.get("successor_merged"), "successor_merged")
    _sha(succession.get("retired_head_sha"), "retired_head_sha")
    _sha(succession.get("successor_head_sha"), "successor_head_sha")
    _sha(succession.get("successor_base_sha"), "successor_base_sha")

    if retired_state != "closed":
        raise AuditError(f"retired gate #{retired_gate} is not closed")
    if retired_merged:
        raise AuditError(f"retired gate #{retired_gate} unexpectedly merged")

    raw_pulls = payload.get("pull_requests")
    if not isinstance(raw_pulls, list):
        raise AuditError("pull_requests must be a list")
    if len(raw_pulls) > MAX_OPEN_PULLS:
        raise AuditError("open PR inventory exceeds bounded limit")

    seen: set[int] = set()
    stale: list[int] = []
    successor_aware: list[int] = []
    serialized_primary: list[int] = []

    primary_marker = f"#{primary_gate}"
    retired_marker = f"#{retired_gate}"
    successor_marker = f"#{successor_gate}"

    for raw in raw_pulls:
        if not isinstance(raw, dict):
            raise AuditError("invalid pull request entry")
        number = _number(raw.get("number"), "pull request number")
        if number in seen:
            raise AuditError(f"duplicate pull request #{number}")
        seen.add(number)

        if _state(raw.get("state"), f"PR #{number} state") != "open":
            raise AuditError(f"non-open PR #{number} in open inventory")
        _bool(raw.get("draft"), f"PR #{number} draft")
        _sha(raw.get("head_sha"), f"PR #{number} head_sha")
        _sha(raw.get("base_sha"), f"PR #{number} base_sha")
        body = _body(raw.get("body"))
        lowered = body.lower()

        # The successor PR necessarily documents the retired predecessor in its
        # own supersession history. That is provenance, not stale coordination.
        if number == successor_gate:
            continue
        if not _serialized_context(body) or primary_marker not in lowered:
            continue

        serialized_primary.append(number)
        if retired_marker in lowered and successor_marker in lowered:
            successor_aware.append(number)
        elif retired_marker in lowered:
            stale.append(number)

    stale.sort()
    successor_aware.sort()
    serialized_primary.sort()

    return {
        "schema": SCHEMA,
        "repository": repository,
        "current_main_sha": current_main_sha,
        "status": "drift_detected" if stale else "clean",
        "primary_gate": primary_gate,
        "retired_gate": retired_gate,
        "successor_gate": successor_gate,
        "successor_open": successor_state == "open" and not successor_merged,
        "open_pr_count": len(raw_pulls),
        "serialized_primary_pr_count": len(serialized_primary),
        "successor_aware_pr_count": len(successor_aware),
        "stale_reference_count": len(stale),
        "stale_reference_prs": stale,
        "mutation_performed": False,
        "release_authorized": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit stale serialized-gate references in open zCloud PR metadata"
    )
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--require-clean", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        result = audit_snapshot(_load_json(args.snapshot))
    except AuditError as exc:
        if args.json:
            print(json.dumps({
                "schema": SCHEMA,
                "status": "invalid",
                "error": str(exc),
                "mutation_performed": False,
                "release_authorized": False,
            }, sort_keys=True))
        else:
            print(f"SERIALIZED_GATE_METADATA_AUDIT_INVALID: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, sort_keys=True, indent=2))
    else:
        print(
            "SERIALIZED_GATE_METADATA_AUDIT "
            f"status={result['status']} "
            f"stale={result['stale_reference_count']} "
            f"successor_aware={result['successor_aware_pr_count']} "
            f"serialized_primary={result['serialized_primary_pr_count']}"
        )

    if args.require_clean and result["stale_reference_count"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

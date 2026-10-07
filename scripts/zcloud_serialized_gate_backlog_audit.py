#!/usr/bin/env python3
"""Read-only audit of the zCloud serialized integration backlog.

The repository intentionally keeps many independently validated PRs unmerged while
high-risk live/control-plane work owns a serialized integration window. This tool
turns that convention into bounded machine-readable evidence without mutating Git,
GitHub, runtime, queue, browser, service, or SQLite state.

Input is a JSON snapshot produced by the companion workflow. PR bodies are consumed
only for dependency classification and are never emitted.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

SCHEMA = "zcloud-serialized-gate-backlog-audit-v2"
DEFAULT_GATE_NUMBERS = (580, 1089)
DEFAULT_DEPENDENCY_GATE_GROUPS = ((580,), (576, 1089))
MAX_SNAPSHOT_BYTES = 4 * 1024 * 1024
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


class SnapshotError(ValueError):
    pass


def _sha(value: Any, field: str) -> str:
    text = str(value or "").strip().lower()
    if not SHA_RE.fullmatch(text):
        raise SnapshotError(f"invalid {field}")
    return text


def _number(value: Any, field: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise SnapshotError(f"invalid {field}") from exc
    if number < 1:
        raise SnapshotError(f"invalid {field}")
    return number


def _bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise SnapshotError(f"invalid {field}")
    return value


def _state(value: Any, field: str) -> str:
    state = str(value or "").strip().lower()
    if state not in {"open", "closed"}:
        raise SnapshotError(f"invalid {field}")
    return state


def _body(value: Any) -> str:
    text = str(value or "")
    if len(text) > MAX_BODY_CHARS:
        raise SnapshotError("PR body exceeds bounded input size")
    return text


def _mentions_gate_dependency(
    body: str,
    gate_groups: tuple[tuple[int, ...], ...],
) -> bool:
    lowered = body.lower()
    if not all(
        any(f"#{number}" in lowered for number in group)
        for group in gate_groups
    ):
        return False
    return any(marker in lowered for marker in SERIALIZED_MARKERS)


def _load_json(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise SnapshotError("snapshot symlink rejected")
    try:
        stat = path.stat()
    except OSError as exc:
        raise SnapshotError(f"snapshot unavailable: {exc}") from exc
    if stat.st_size > MAX_SNAPSHOT_BYTES:
        raise SnapshotError("snapshot exceeds bounded input size")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SnapshotError(f"invalid snapshot JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise SnapshotError("snapshot must be a JSON object")
    return payload


def audit_snapshot(
    payload: dict[str, Any],
    *,
    gate_numbers: tuple[int, ...] = DEFAULT_GATE_NUMBERS,
    dependency_gate_groups: tuple[tuple[int, ...], ...] | None = None,
) -> dict[str, Any]:
    current_main_sha = _sha(payload.get("current_main_sha"), "current_main_sha")
    repository = str(payload.get("repository") or "").strip()
    if not repository or len(repository) > 200 or "/" not in repository:
        raise SnapshotError("invalid repository")
    inventory_complete = _bool(payload.get("inventory_complete"), "inventory_complete")
    if not inventory_complete:
        raise SnapshotError("open PR inventory incomplete")

    raw_gates = payload.get("gates")
    if not isinstance(raw_gates, list):
        raise SnapshotError("gates must be a list")
    gate_map: dict[int, dict[str, Any]] = {}
    for raw in raw_gates:
        if not isinstance(raw, dict):
            raise SnapshotError("invalid gate entry")
        number = _number(raw.get("number"), "gate number")
        if number in gate_map:
            raise SnapshotError(f"duplicate gate #{number}")
        gate_map[number] = raw

    missing_gates = [number for number in gate_numbers if number not in gate_map]
    if missing_gates:
        raise SnapshotError(
            "missing gate snapshot(s): " + ",".join(str(number) for number in missing_gates)
        )

    gates = []
    gate_open = False
    for number in gate_numbers:
        raw = gate_map[number]
        state = _state(raw.get("state"), f"gate #{number} state")
        merged = _bool(raw.get("merged"), f"gate #{number} merged")
        draft = _bool(raw.get("draft"), f"gate #{number} draft")
        head_sha = _sha(raw.get("head_sha"), f"gate #{number} head_sha")
        base_sha = _sha(raw.get("base_sha"), f"gate #{number} base_sha")
        is_open = state == "open" and not merged
        gate_open = gate_open or is_open
        gates.append(
            {
                "number": number,
                "state": state,
                "merged": merged,
                "draft": draft,
                "head_sha": head_sha,
                "base_sha": base_sha,
                "base_is_current_main": base_sha == current_main_sha,
                "open": is_open,
            }
        )

    raw_pulls = payload.get("pull_requests")
    if not isinstance(raw_pulls, list):
        raise SnapshotError("pull_requests must be a list")
    if len(raw_pulls) > MAX_OPEN_PULLS:
        raise SnapshotError("open PR inventory exceeds bounded limit")

    seen: set[int] = set()
    dependents: list[dict[str, Any]] = []
    gate_set = set(gate_numbers)
    if dependency_gate_groups is None:
        dependency_gate_groups = (
            DEFAULT_DEPENDENCY_GATE_GROUPS
            if gate_numbers == DEFAULT_GATE_NUMBERS
            else tuple((number,) for number in gate_numbers)
        )
    if not dependency_gate_groups or any(not group for group in dependency_gate_groups):
        raise SnapshotError("invalid dependency gate groups")
    for raw in raw_pulls:
        if not isinstance(raw, dict):
            raise SnapshotError("invalid pull request entry")
        number = _number(raw.get("number"), "pull request number")
        if number in seen:
            raise SnapshotError(f"duplicate pull request #{number}")
        seen.add(number)

        state = _state(raw.get("state"), f"PR #{number} state")
        if state != "open":
            raise SnapshotError(f"non-open PR #{number} in open inventory")
        draft = _bool(raw.get("draft"), f"PR #{number} draft")
        head_sha = _sha(raw.get("head_sha"), f"PR #{number} head_sha")
        base_sha = _sha(raw.get("base_sha"), f"PR #{number} base_sha")
        body = _body(raw.get("body"))
        if number in gate_set:
            continue
        if not _mentions_gate_dependency(body, dependency_gate_groups):
            continue

        dependents.append(
            {
                "number": number,
                "draft": draft,
                "head_sha": head_sha,
                "base_sha": base_sha,
                "base_is_current_main": base_sha == current_main_sha,
                "review_ready_current_main": (not draft and base_sha == current_main_sha),
            }
        )

    dependents.sort(key=lambda item: item["number"])
    review_ready_current_main = [
        item for item in dependents if item["review_ready_current_main"]
    ]
    stale_base = [item for item in dependents if not item["base_is_current_main"]]
    drafts = [item for item in dependents if item["draft"]]

    if gate_open:
        status = "blocked"
    elif dependents:
        status = "released_with_backlog"
    else:
        status = "clear"

    return {
        "schema": SCHEMA,
        "repository": repository,
        "current_main_sha": current_main_sha,
        "inventory_complete": True,
        "status": status,
        "gate_open": gate_open,
        "integration_released": not gate_open,
        "gates": gates,
        "dependent_pr_count": len(dependents),
        "review_ready_current_main_count": len(review_ready_current_main),
        "stale_base_count": len(stale_base),
        "draft_count": len(drafts),
        "dependents": dependents,
        "mutation_performed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit PR backlog behind the zCloud serialized integration gates"
    )
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument(
        "--gate-pr",
        action="append",
        type=int,
        dest="gate_prs",
        help="Gate PR number; repeat to override defaults",
    )
    parser.add_argument("--require-released", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    gate_numbers = tuple(args.gate_prs or DEFAULT_GATE_NUMBERS)
    if len(gate_numbers) != len(set(gate_numbers)) or any(number < 1 for number in gate_numbers):
        raise SystemExit("invalid gate PR configuration")

    try:
        payload = _load_json(args.snapshot)
        result = audit_snapshot(payload, gate_numbers=gate_numbers)
    except SnapshotError as exc:
        if args.json:
            print(
                json.dumps(
                    {
                        "schema": SCHEMA,
                        "status": "invalid",
                        "error": str(exc),
                        "mutation_performed": False,
                    },
                    sort_keys=True,
                )
            )
        else:
            print(f"SERIALIZED_GATE_BACKLOG_INVALID: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, sort_keys=True, indent=2))
    else:
        print(
            "SERIALIZED_GATE_BACKLOG "
            f"status={result['status']} "
            f"dependents={result['dependent_pr_count']} "
            f"review_ready_current_main={result['review_ready_current_main_count']} "
            f"stale_base={result['stale_base_count']} "
            f"drafts={result['draft_count']}"
        )

    if args.require_released and not result["integration_released"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

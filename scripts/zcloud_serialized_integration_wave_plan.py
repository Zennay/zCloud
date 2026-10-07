#!/usr/bin/env python3
"""Read-only, file-disjoint post-gate integration planner for zCloud.

The planner consumes a bounded GitHub snapshot, validates the serialized #580/#1089
gate, identifies explicit dependents, and uses complete changed-file evidence to
partition current-main review-ready PRs into deterministic file-disjoint waves.
It never authorizes or performs a merge or any runtime mutation.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

SCHEMA = "zcloud-serialized-integration-wave-plan-v3"
DEFAULT_GATE_NUMBERS = (580, 1089)
DEFAULT_DEPENDENCY_GATE_GROUPS = ((580,), (576, 1089))
MAX_INPUT_BYTES = 8 * 1024 * 1024
MAX_OPEN_PULLS = 500
MAX_READY_CANDIDATES = 200
MAX_FILES_PER_PR = 500
MAX_BODY_CHARS = 100_000
MAX_WAVE_SIZE = 25
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SERIALIZED_MARKERS = (
    "serialized",
    "keep unmerged",
    "integration window",
    "live writer",
    "production/control-plane window",
    "control-plane window",
)


class PlanError(ValueError):
    pass


def _sha(value: Any, field: str) -> str:
    text = str(value or "").strip().lower()
    if not SHA_RE.fullmatch(text):
        raise PlanError(f"invalid {field}")
    return text


def _number(value: Any, field: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise PlanError(f"invalid {field}") from exc
    if number < 1:
        raise PlanError(f"invalid {field}")
    return number


def _bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise PlanError(f"invalid {field}")
    return value


def _state(value: Any, field: str) -> str:
    state = str(value or "").strip().lower()
    if state not in {"open", "closed"}:
        raise PlanError(f"invalid {field}")
    return state


def _body(value: Any) -> str:
    text = str(value or "")
    if len(text) > MAX_BODY_CHARS:
        raise PlanError("PR body exceeds bounded input size")
    return text


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


def _is_serialized_dependency(body: str, gate_numbers: tuple[int, ...]) -> bool:
    lowered = body.lower()
    gate_groups = (
        DEFAULT_DEPENDENCY_GATE_GROUPS
        if gate_numbers == DEFAULT_GATE_NUMBERS
        else tuple((number,) for number in gate_numbers)
    )
    if not all(
        any(f"#{number}" in lowered for number in group)
        for group in gate_groups
    ):
        return False
    return any(marker in lowered for marker in SERIALIZED_MARKERS)


def _load_json(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink():
        raise PlanError(f"{label} symlink rejected")
    try:
        stat = path.stat()
    except OSError as exc:
        raise PlanError(f"{label} unavailable: {exc}") from exc
    if stat.st_size > MAX_INPUT_BYTES:
        raise PlanError(f"{label} exceeds bounded input size")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PlanError(f"invalid {label} JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise PlanError(f"{label} must be a JSON object")
    return payload


def classify_snapshot(
    payload: dict[str, Any],
    *,
    gate_numbers: tuple[int, ...] = DEFAULT_GATE_NUMBERS,
) -> dict[str, Any]:
    repository = str(payload.get("repository") or "").strip()
    if not repository or len(repository) > 200 or "/" not in repository:
        raise PlanError("invalid repository")
    current_main_sha = _sha(payload.get("current_main_sha"), "current_main_sha")
    if not _bool(payload.get("inventory_complete"), "inventory_complete"):
        raise PlanError("open PR inventory incomplete")

    raw_gates = payload.get("gates")
    if not isinstance(raw_gates, list):
        raise PlanError("gates must be a list")
    gate_map: dict[int, dict[str, Any]] = {}
    for raw in raw_gates:
        if not isinstance(raw, dict):
            raise PlanError("invalid gate entry")
        number = _number(raw.get("number"), "gate number")
        if number in gate_map:
            raise PlanError(f"duplicate gate #{number}")
        gate_map[number] = raw

    missing = [number for number in gate_numbers if number not in gate_map]
    if missing:
        raise PlanError("missing gate snapshot(s): " + ",".join(map(str, missing)))

    gate_rows: list[dict[str, Any]] = []
    gate_open = False
    for number in gate_numbers:
        raw = gate_map[number]
        state = _state(raw.get("state"), f"gate #{number} state")
        merged = _bool(raw.get("merged"), f"gate #{number} merged")
        draft = _bool(raw.get("draft"), f"gate #{number} draft")
        head_sha = _sha(raw.get("head_sha"), f"gate #{number} head_sha")
        base_sha = _sha(raw.get("base_sha"), f"gate #{number} base_sha")
        if state == "open" and merged:
            raise PlanError(f"incoherent gate #{number}: open and merged")
        is_open = state == "open" and not merged
        gate_open = gate_open or is_open
        gate_rows.append(
            {
                "number": number,
                "state": state,
                "merged": merged,
                "draft": draft,
                "head_sha": head_sha,
                "base_sha": base_sha,
                "open": is_open,
                "base_is_current_main": base_sha == current_main_sha,
            }
        )

    raw_pulls = payload.get("pull_requests")
    if not isinstance(raw_pulls, list):
        raise PlanError("pull_requests must be a list")
    if len(raw_pulls) > MAX_OPEN_PULLS:
        raise PlanError("open PR inventory exceeds bounded limit")

    gate_set = set(gate_numbers)
    seen: set[int] = set()
    ready: list[int] = []
    stale: list[int] = []
    drafts: list[int] = []
    dependent_count = 0

    for raw in raw_pulls:
        if not isinstance(raw, dict):
            raise PlanError("invalid pull request entry")
        number = _number(raw.get("number"), "pull request number")
        if number in seen:
            raise PlanError(f"duplicate pull request #{number}")
        seen.add(number)

        state = _state(raw.get("state"), f"PR #{number} state")
        if state != "open":
            raise PlanError(f"non-open PR #{number} in open inventory")
        draft = _bool(raw.get("draft"), f"PR #{number} draft")
        _sha(raw.get("head_sha"), f"PR #{number} head_sha")
        base_sha = _sha(raw.get("base_sha"), f"PR #{number} base_sha")
        body = _body(raw.get("body"))

        if number in gate_set or not _is_serialized_dependency(body, gate_numbers):
            continue

        dependent_count += 1
        if draft:
            drafts.append(number)
        if base_sha != current_main_sha:
            stale.append(number)
        if not draft and base_sha == current_main_sha:
            ready.append(number)

    ready.sort()
    stale.sort()
    drafts.sort()
    if len(ready) > MAX_READY_CANDIDATES:
        raise PlanError("ready candidate inventory exceeds bounded limit")

    return {
        "repository": repository,
        "current_main_sha": current_main_sha,
        "gate_open": gate_open,
        "integration_released": not gate_open,
        "gates": gate_rows,
        "dependent_pr_count": dependent_count,
        "ready_pull_requests": ready,
        "stale_base_pull_requests": stale,
        "draft_pull_requests": drafts,
    }


def _validated_files(
    files_by_pr: dict[str, Any],
    ready_pull_requests: list[int],
) -> list[dict[str, Any]]:
    expected = {str(number) for number in ready_pull_requests}
    if set(files_by_pr) != expected:
        raise PlanError("changed-file evidence key set mismatch")

    candidates: list[dict[str, Any]] = []
    for number in ready_pull_requests:
        raw = files_by_pr.get(str(number))
        if not isinstance(raw, dict):
            raise PlanError(f"missing changed-file evidence for PR #{number}")
        if raw.get("complete") is not True:
            raise PlanError(f"incomplete changed-file evidence for PR #{number}")
        files = raw.get("files")
        if not isinstance(files, list) or not files:
            raise PlanError(f"invalid changed-file evidence for PR #{number}")
        if len(files) > MAX_FILES_PER_PR:
            raise PlanError(f"changed-file evidence exceeds limit for PR #{number}")
        normalized = [_safe_path(path) for path in files]
        if len(normalized) != len(set(normalized)):
            raise PlanError(f"duplicate changed path for PR #{number}")
        candidates.append(
            {
                "number": number,
                "files": tuple(sorted(normalized)),
                "file_count": len(normalized),
            }
        )
    candidates.sort(key=lambda row: (row["file_count"], row["number"]))
    return candidates


def _partition_waves(
    candidates: list[dict[str, Any]],
    *,
    wave_size: int,
) -> list[dict[str, Any]]:
    remaining = list(candidates)
    waves: list[dict[str, Any]] = []
    wave_number = 1

    while remaining:
        selected: list[dict[str, Any]] = []
        deferred: list[dict[str, Any]] = []
        used_paths: set[str] = set()

        for row in remaining:
            has_conflict = any(path in used_paths for path in row["files"])
            if has_conflict or len(selected) >= wave_size:
                deferred.append(row)
                continue
            selected.append(row)
            used_paths.update(row["files"])

        if not selected:
            raise PlanError("unable to construct a non-empty integration wave")
        waves.append(
            {
                "wave": wave_number,
                "pull_requests": [
                    {"number": row["number"], "file_count": row["file_count"]}
                    for row in selected
                ],
                "file_disjoint": True,
                "requires_revalidation_after_wave": True,
            }
        )
        remaining = deferred
        wave_number += 1

    return waves


def build_plan(
    snapshot: dict[str, Any],
    files_by_pr: dict[str, Any],
    *,
    gate_numbers: tuple[int, ...] = DEFAULT_GATE_NUMBERS,
    wave_size: int = 10,
) -> dict[str, Any]:
    if wave_size < 1 or wave_size > MAX_WAVE_SIZE:
        raise PlanError(f"wave_size must be 1..{MAX_WAVE_SIZE}")
    if not isinstance(files_by_pr, dict):
        raise PlanError("files_by_pr must be an object")

    classified = classify_snapshot(snapshot, gate_numbers=gate_numbers)
    candidates = _validated_files(files_by_pr, classified["ready_pull_requests"])
    waves = _partition_waves(candidates, wave_size=wave_size)

    if not candidates:
        status = "clear" if classified["dependent_pr_count"] == 0 else "no_current_main_candidates"
    elif classified["gate_open"]:
        status = "blocked_preview"
    else:
        status = "released_candidate_waves"

    return {
        "schema": SCHEMA,
        "repository": classified["repository"],
        "current_main_sha": classified["current_main_sha"],
        "inventory_complete": True,
        "status": status,
        "integration_released": classified["integration_released"],
        "preview_only": classified["gate_open"],
        "executable": classified["integration_released"] and bool(candidates),
        "merge_authorized": False,
        "auto_merge_allowed": False,
        "ci_revalidation_required": True,
        "ownership_revalidation_required": True,
        "requires_per_pr_revalidation": True,
        "revalidation_rule": "before each PR merge require fresh CI, ownership, non-draft state, and base_sha == then-current main",
        "wave_size": wave_size,
        "gate_numbers": list(gate_numbers),
        "gates": classified["gates"],
        "dependent_pr_count": classified["dependent_pr_count"],
        "ready_current_main_count": len(candidates),
        "stale_base_count": len(classified["stale_base_pull_requests"]),
        "draft_count": len(classified["draft_pull_requests"]),
        "stale_base_pull_requests": classified["stale_base_pull_requests"],
        "draft_pull_requests": classified["draft_pull_requests"],
        "wave_count": len(waves),
        "waves": waves,
        "mutation_performed": False,
    }


def _parse_gate_numbers(values: list[int] | None) -> tuple[int, ...]:
    gate_numbers = tuple(values or DEFAULT_GATE_NUMBERS)
    if len(gate_numbers) != len(set(gate_numbers)) or any(number < 1 for number in gate_numbers):
        raise PlanError("invalid gate PR configuration")
    return gate_numbers


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Plan deterministic file-disjoint waves for zCloud serialized integration"
    )
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--files", type=Path)
    parser.add_argument("--list-ready", action="store_true")
    parser.add_argument("--wave-size", type=int, default=10)
    parser.add_argument("--gate-pr", action="append", type=int, dest="gate_prs")
    parser.add_argument("--require-released", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        gate_numbers = _parse_gate_numbers(args.gate_prs)
        snapshot = _load_json(args.snapshot, "snapshot")
        classified = classify_snapshot(snapshot, gate_numbers=gate_numbers)

        if args.list_ready:
            result = {
                "schema": SCHEMA,
                "mode": "ready_inventory",
                "current_main_sha": classified["current_main_sha"],
                "gate_open": classified["gate_open"],
                "ready_pull_requests": classified["ready_pull_requests"],
                "mutation_performed": False,
            }
        else:
            if args.files is None:
                raise PlanError("--files is required unless --list-ready is used")
            files_payload = _load_json(args.files, "changed-file evidence")
            result = build_plan(
                snapshot,
                files_payload,
                gate_numbers=gate_numbers,
                wave_size=args.wave_size,
            )
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
            print(f"SERIALIZED_INTEGRATION_PLAN_INVALID: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, sort_keys=True, indent=2))
    else:
        if args.list_ready:
            print(
                "SERIALIZED_INTEGRATION_READY "
                f"count={len(result['ready_pull_requests'])}"
            )
        else:
            print(
                "SERIALIZED_INTEGRATION_PLAN "
                f"status={result['status']} "
                f"ready={result['ready_current_main_count']} "
                f"waves={result['wave_count']} "
                f"stale={result['stale_base_count']} "
                f"drafts={result['draft_count']}"
            )

    if args.require_released and not args.list_ready and not result["integration_released"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

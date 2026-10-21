#!/usr/bin/env python3
"""Plan deterministic, bounded remediation batches for stale serialized-gate PR metadata.

Input is the bounded read-only report produced by the serialized-gate metadata
audit contract. This planner never edits pull requests or repository/runtime
state. It emits PR-number-only batches for later explicit review.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

INPUT_SCHEMA = "zcloud-serialized-gate-metadata-drift-audit-v1"
OUTPUT_SCHEMA = "zcloud-serialized-gate-metadata-remediation-plan-v1"
MAX_INPUT_BYTES = 1024 * 1024
MAX_STALE_REFERENCES = 500
MAX_BATCH_SIZE = 50
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class PlanError(ValueError):
    pass


def _positive_int(value: Any, field: str) -> int:
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


def _sha(value: Any, field: str) -> str:
    text = str(value or "").strip().lower()
    if not SHA_RE.fullmatch(text):
        raise PlanError(f"invalid {field}")
    return text


def _load_json(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise PlanError("audit report symlink rejected")
    try:
        stat = path.stat()
    except OSError as exc:
        raise PlanError(f"audit report unavailable: {exc}") from exc
    if stat.st_size > MAX_INPUT_BYTES:
        raise PlanError("audit report exceeds bounded input size")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PlanError(f"invalid audit report JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise PlanError("audit report must be a JSON object")
    return payload


def plan_remediation(payload: dict[str, Any], *, batch_size: int = 20) -> dict[str, Any]:
    if payload.get("schema") != INPUT_SCHEMA:
        raise PlanError("unexpected audit schema")
    if batch_size < 1 or batch_size > MAX_BATCH_SIZE:
        raise PlanError(f"batch_size must be between 1 and {MAX_BATCH_SIZE}")

    repository = str(payload.get("repository") or "").strip()
    if not repository or "/" not in repository or len(repository) > 200:
        raise PlanError("invalid repository")
    current_main_sha = _sha(payload.get("current_main_sha"), "current_main_sha")

    status = str(payload.get("status") or "").strip()
    if status not in {"clean", "drift_detected"}:
        raise PlanError("invalid audit status")

    primary_gate = _positive_int(payload.get("primary_gate"), "primary_gate")
    retired_gate = _positive_int(payload.get("retired_gate"), "retired_gate")
    successor_gate = _positive_int(payload.get("successor_gate"), "successor_gate")
    if len({primary_gate, retired_gate, successor_gate}) != 3:
        raise PlanError("gate identities must be distinct")

    successor_open = _bool(payload.get("successor_open"), "successor_open")
    if _bool(payload.get("mutation_performed"), "mutation_performed"):
        raise PlanError("source audit unexpectedly mutated state")
    if _bool(payload.get("release_authorized"), "release_authorized"):
        raise PlanError("source audit unexpectedly authorized release")

    stale_count = int(payload.get("stale_reference_count", -1))
    raw_stale = payload.get("stale_reference_prs")
    if not isinstance(raw_stale, list):
        raise PlanError("stale_reference_prs must be a list")
    if len(raw_stale) > MAX_STALE_REFERENCES:
        raise PlanError("stale reference inventory exceeds bounded limit")

    stale: list[int] = []
    seen: set[int] = set()
    for raw in raw_stale:
        number = _positive_int(raw, "stale PR number")
        if number in seen:
            raise PlanError(f"duplicate stale PR #{number}")
        seen.add(number)
        stale.append(number)
    stale.sort()

    if stale_count != len(stale):
        raise PlanError("stale_reference_count mismatch")
    if status == "clean" and stale:
        raise PlanError("clean audit contains stale references")
    if status == "drift_detected" and not stale:
        raise PlanError("drift audit has no stale references")
    if stale and not successor_open:
        raise PlanError("successor gate is not open; remediation plan requires refresh")

    batches = []
    for offset in range(0, len(stale), batch_size):
        numbers = stale[offset : offset + batch_size]
        batches.append(
            {
                "batch_index": len(batches) + 1,
                "count": len(numbers),
                "pr_numbers": numbers,
            }
        )

    return {
        "schema": OUTPUT_SCHEMA,
        "repository": repository,
        "current_main_sha": current_main_sha,
        "status": "remediation_needed" if stale else "clean",
        "primary_gate": primary_gate,
        "retired_gate": retired_gate,
        "successor_gate": successor_gate,
        "stale_reference_count": len(stale),
        "batch_size": batch_size,
        "batch_count": len(batches),
        "batches": batches,
        "requires_fresh_revalidation": True,
        "metadata_write_authorized": False,
        "merge_authorized": False,
        "deploy_authorized": False,
        "mutation_performed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Plan bounded PR-number-only remediation batches for stale serialized-gate metadata"
    )
    parser.add_argument("--audit-report", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        result = plan_remediation(
            _load_json(args.audit_report),
            batch_size=args.batch_size,
        )
    except (PlanError, ValueError) as exc:
        if args.json:
            print(
                json.dumps(
                    {
                        "schema": OUTPUT_SCHEMA,
                        "status": "invalid",
                        "error": str(exc),
                        "metadata_write_authorized": False,
                        "merge_authorized": False,
                        "deploy_authorized": False,
                        "mutation_performed": False,
                    },
                    sort_keys=True,
                )
            )
        else:
            print(f"SERIALIZED_GATE_METADATA_PLAN_INVALID: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, sort_keys=True, indent=2))
    else:
        print(
            "SERIALIZED_GATE_METADATA_REMEDIATION_PLAN "
            f"status={result['status']} "
            f"stale={result['stale_reference_count']} "
            f"batches={result['batch_count']} "
            f"batch_size={result['batch_size']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

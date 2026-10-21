#!/usr/bin/env python3
"""Read-only preflight for a bounded serialized-gate metadata remediation batch.

The planner intentionally emits only pull-request numbers. Immediately before
any later metadata remediation, those numbers must be checked against fresh
GitHub evidence so stale plans cannot silently act on closed, changed, or
successor-aware pull requests. This module performs that check and never
authorizes or performs a write.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

PLAN_SCHEMA = "zcloud-serialized-gate-metadata-remediation-plan-v1"
SNAPSHOT_SCHEMA = "zcloud-serialized-gate-metadata-batch-preflight-snapshot-v1"
OUTPUT_SCHEMA = "zcloud-serialized-gate-metadata-batch-preflight-v1"
MAX_INPUT_BYTES = 4 * 1024 * 1024
MAX_BATCH_SIZE = 50
MAX_PULLS = 500
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


class PreflightError(ValueError):
    pass


def _positive_int(value: Any, field: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise PreflightError(f"invalid {field}") from exc
    if number < 1:
        raise PreflightError(f"invalid {field}")
    return number


def _bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise PreflightError(f"invalid {field}")
    return value


def _sha(value: Any, field: str) -> str:
    text = str(value or "").strip().lower()
    if not SHA_RE.fullmatch(text):
        raise PreflightError(f"invalid {field}")
    return text


def _body(value: Any) -> str:
    text = str(value or "")
    if len(text) > MAX_BODY_CHARS:
        raise PreflightError("PR body exceeds bounded input size")
    return text


def _load_json(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink():
        raise PreflightError(f"{label} symlink rejected")
    try:
        stat = path.stat()
    except OSError as exc:
        raise PreflightError(f"{label} unavailable: {exc}") from exc
    if stat.st_size > MAX_INPUT_BYTES:
        raise PreflightError(f"{label} exceeds bounded input size")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PreflightError(f"invalid {label} JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise PreflightError(f"{label} must be a JSON object")
    return payload


def _serialized_context(body: str) -> bool:
    lowered = body.lower()
    return any(marker in lowered for marker in SERIALIZED_MARKERS)


def _validate_plan(plan: dict[str, Any]) -> tuple[str, str, int, int, int, list[dict[str, Any]]]:
    if plan.get("schema") != PLAN_SCHEMA:
        raise PreflightError("unexpected plan schema")
    repository = str(plan.get("repository") or "").strip()
    if not repository or "/" not in repository or len(repository) > 200:
        raise PreflightError("invalid plan repository")
    current_main_sha = _sha(plan.get("current_main_sha"), "plan current_main_sha")
    primary_gate = _positive_int(plan.get("primary_gate"), "primary_gate")
    retired_gate = _positive_int(plan.get("retired_gate"), "retired_gate")
    successor_gate = _positive_int(plan.get("successor_gate"), "successor_gate")
    if len({primary_gate, retired_gate, successor_gate}) != 3:
        raise PreflightError("gate identities must be distinct")

    for field in (
        "metadata_write_authorized",
        "merge_authorized",
        "deploy_authorized",
        "mutation_performed",
    ):
        if _bool(plan.get(field), f"plan {field}"):
            raise PreflightError(f"plan unexpectedly sets {field}=true")

    status = str(plan.get("status") or "").strip()
    if status not in {"clean", "remediation_needed"}:
        raise PreflightError("invalid plan status")
    batches = plan.get("batches")
    if not isinstance(batches, list):
        raise PreflightError("plan batches must be a list")
    if status == "clean" and batches:
        raise PreflightError("clean plan contains remediation batches")
    if len(batches) > MAX_PULLS:
        raise PreflightError("too many plan batches")
    return repository, current_main_sha, primary_gate, retired_gate, successor_gate, batches


def preflight_batch(
    plan: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    batch_index: int,
) -> dict[str, Any]:
    (
        repository,
        planned_main_sha,
        primary_gate,
        retired_gate,
        successor_gate,
        batches,
    ) = _validate_plan(plan)

    if snapshot.get("schema") != SNAPSHOT_SCHEMA:
        raise PreflightError("unexpected snapshot schema")
    if str(snapshot.get("repository") or "").strip() != repository:
        raise PreflightError("plan/snapshot repository mismatch")
    live_main_sha = _sha(snapshot.get("current_main_sha"), "snapshot current_main_sha")
    if live_main_sha != planned_main_sha:
        raise PreflightError("plan main SHA is stale")
    if _positive_int(snapshot.get("successor_gate"), "snapshot successor_gate") != successor_gate:
        raise PreflightError("successor gate mismatch")
    if not _bool(snapshot.get("successor_open"), "snapshot successor_open"):
        raise PreflightError("successor gate is no longer open")

    if str(plan.get("status")) == "clean":
        return {
            "schema": OUTPUT_SCHEMA,
            "repository": repository,
            "current_main_sha": live_main_sha,
            "status": "clean",
            "batch_index": None,
            "planned_count": 0,
            "eligible_count": 0,
            "ineligible_count": 0,
            "eligible": [],
            "ineligible": [],
            "requires_fresh_revalidation": True,
            "metadata_write_authorized": False,
            "merge_authorized": False,
            "deploy_authorized": False,
            "mutation_performed": False,
        }

    batch_index = _positive_int(batch_index, "batch_index")
    if batch_index > len(batches):
        raise PreflightError("batch_index outside plan")

    raw_batch = batches[batch_index - 1]
    if not isinstance(raw_batch, dict):
        raise PreflightError("invalid plan batch")
    if _positive_int(raw_batch.get("batch_index"), "plan batch_index") != batch_index:
        raise PreflightError("plan batch index mismatch")
    raw_numbers = raw_batch.get("pr_numbers")
    if not isinstance(raw_numbers, list):
        raise PreflightError("plan batch pr_numbers must be a list")
    if not raw_numbers or len(raw_numbers) > MAX_BATCH_SIZE:
        raise PreflightError("plan batch size outside bounded limit")

    planned: list[int] = []
    seen_planned: set[int] = set()
    for raw in raw_numbers:
        number = _positive_int(raw, "planned PR number")
        if number in seen_planned:
            raise PreflightError(f"duplicate planned PR #{number}")
        if number == successor_gate:
            raise PreflightError("successor gate cannot be a remediation target")
        seen_planned.add(number)
        planned.append(number)
    if int(raw_batch.get("count", -1)) != len(planned):
        raise PreflightError("plan batch count mismatch")

    raw_pulls = snapshot.get("pull_requests")
    if not isinstance(raw_pulls, list) or len(raw_pulls) > MAX_PULLS:
        raise PreflightError("invalid bounded live PR inventory")

    live: dict[int, dict[str, Any]] = {}
    for raw in raw_pulls:
        if not isinstance(raw, dict):
            raise PreflightError("invalid live PR entry")
        number = _positive_int(raw.get("number"), "live PR number")
        if number in live:
            raise PreflightError(f"duplicate live PR #{number}")
        state = str(raw.get("state") or "").strip().lower()
        if state not in {"open", "closed"}:
            raise PreflightError(f"invalid live PR #{number} state")
        draft = _bool(raw.get("draft"), f"live PR #{number} draft")
        head_sha = _sha(raw.get("head_sha"), f"live PR #{number} head_sha")
        body = _body(raw.get("body"))
        live[number] = {
            "state": state,
            "draft": draft,
            "head_sha": head_sha,
            "body": body,
        }

    eligible: list[dict[str, Any]] = []
    ineligible: list[dict[str, Any]] = []
    primary_marker = f"#{primary_gate}"
    retired_marker = f"#{retired_gate}"
    successor_marker = f"#{successor_gate}"

    for number in planned:
        item = live.get(number)
        if item is None:
            ineligible.append({"number": number, "reason": "missing"})
            continue
        if item["state"] != "open":
            ineligible.append({"number": number, "reason": "closed"})
            continue
        lowered = item["body"].lower()
        if successor_marker in lowered:
            ineligible.append({"number": number, "reason": "successor_aware"})
            continue
        if retired_marker not in lowered:
            ineligible.append({"number": number, "reason": "retired_reference_removed"})
            continue
        if primary_marker not in lowered or not _serialized_context(item["body"]):
            ineligible.append({"number": number, "reason": "serialized_context_removed"})
            continue
        eligible.append(
            {
                "number": number,
                "head_sha": item["head_sha"],
                "draft": item["draft"],
            }
        )

    return {
        "schema": OUTPUT_SCHEMA,
        "repository": repository,
        "current_main_sha": live_main_sha,
        "status": "ready_for_review" if not ineligible else "refresh_required",
        "batch_index": batch_index,
        "planned_count": len(planned),
        "eligible_count": len(eligible),
        "ineligible_count": len(ineligible),
        "eligible": eligible,
        "ineligible": ineligible,
        "requires_fresh_revalidation": True,
        "metadata_write_authorized": False,
        "merge_authorized": False,
        "deploy_authorized": False,
        "mutation_performed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Preflight one bounded stale serialized-gate metadata remediation batch"
    )
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--batch-index", type=int, default=1)
    parser.add_argument("--require-ready", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        result = preflight_batch(
            _load_json(args.plan, "plan"),
            _load_json(args.snapshot, "snapshot"),
            batch_index=args.batch_index,
        )
    except PreflightError as exc:
        if args.json:
            print(json.dumps({
                "schema": OUTPUT_SCHEMA,
                "status": "invalid",
                "error": str(exc),
                "metadata_write_authorized": False,
                "merge_authorized": False,
                "deploy_authorized": False,
                "mutation_performed": False,
            }, sort_keys=True))
        else:
            print(f"SERIALIZED_GATE_METADATA_BATCH_PREFLIGHT_INVALID: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, sort_keys=True, indent=2))
    else:
        print(
            "SERIALIZED_GATE_METADATA_BATCH_PREFLIGHT "
            f"status={result['status']} "
            f"eligible={result['eligible_count']} "
            f"ineligible={result['ineligible_count']}"
        )
    if args.require_ready and result["status"] not in {"ready_for_review", "clean"}:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

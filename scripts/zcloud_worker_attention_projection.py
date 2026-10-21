#!/usr/bin/env python3
"""Project bounded worker-attention cycle evidence into privacy-safe control-plane metrics."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

POLICY = "zcloud-worker-attention-projection-v1"
MAX_BYTES = 128 * 1024
MAX_CYCLES = 256
MAX_MATERIAL_OUTPUTS = 8
MAX_CYCLE_SECONDS = 4 * 60 * 60
FUTURE_SKEW_SECONDS = 5

TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")

CATEGORIES = (
    "feature_code",
    "bugfix",
    "architecture",
    "validation",
    "polling_waiting",
    "audit",
    "recovery_retry",
)
USEFUL_CATEGORIES = frozenset({"feature_code", "bugfix", "architecture"})
VPS_DELEGABLE_CATEGORIES = frozenset(
    {"validation", "polling_waiting", "audit", "recovery_retry"}
)
MATERIAL_OUTPUTS = frozenset(
    {
        "commit",
        "code_change",
        "task_completed",
        "milestone_moved",
        "blocker_removed",
        "deploy_success",
        "deploy_failure",
    }
)
USEFUL_OUTPUTS = frozenset({"task_completed", "milestone_moved", "blocker_removed"})

TOP_KEYS = {"schema_version", "captured_at", "cycles"}
CYCLE_KEYS = {
    "worker_id",
    "cycle_id",
    "started_at",
    "ended_at",
    "category",
    "vps_delegable",
    "material_outputs",
}


class AttentionError(ValueError):
    """Bounded fail-closed worker-attention evidence error."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _timestamp(value: Any, code: str) -> dt.datetime:
    if not isinstance(value, str) or not value or len(value) > 64:
        raise AttentionError(code)
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AttentionError(code) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AttentionError(code)
    return parsed.astimezone(dt.timezone.utc)


def _token(value: Any, code: str) -> str:
    if not isinstance(value, str) or not TOKEN_RE.fullmatch(value):
        raise AttentionError(code)
    return value


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _percentage(numerator: int, denominator: int) -> float:
    return round((numerator / denominator) * 100.0, 2) if denominator else 0.0


def load_snapshot(path: Path) -> dict[str, Any]:
    try:
        stat = path.lstat()
    except OSError as exc:
        raise AttentionError("snapshot_unreadable") from exc
    if path.is_symlink():
        raise AttentionError("snapshot_symlink_rejected")
    if not path.is_file():
        raise AttentionError("snapshot_not_regular")
    if stat.st_size > MAX_BYTES:
        raise AttentionError("snapshot_too_large")
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise AttentionError("snapshot_unreadable") from exc
    if len(raw.encode("utf-8")) > MAX_BYTES:
        raise AttentionError("snapshot_too_large")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AttentionError("snapshot_invalid_json") from exc
    if not isinstance(payload, dict):
        raise AttentionError("snapshot_not_object")
    return payload


def _parse_window(
    *,
    window_start: str | None,
    window_end: str | None,
) -> tuple[dt.datetime | None, dt.datetime | None]:
    start = _timestamp(window_start, "window_start_invalid") if window_start else None
    end = _timestamp(window_end, "window_end_invalid") if window_end else None
    if start and end and end < start:
        raise AttentionError("window_order_invalid")
    return start, end


def project_snapshot(
    snapshot: dict[str, Any],
    *,
    now: dt.datetime | None = None,
    max_age_seconds: int = 86400,
    window_start: str | None = None,
    window_end: str | None = None,
) -> dict[str, Any]:
    if not 1 <= max_age_seconds <= 7 * 86400:
        raise AttentionError("max_age_invalid")
    if set(snapshot) != TOP_KEYS:
        raise AttentionError("snapshot_keys_invalid")
    if type(snapshot["schema_version"]) is not int or snapshot["schema_version"] != 1:
        raise AttentionError("schema_version_unsupported")

    now_utc = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
    captured_at = _timestamp(snapshot["captured_at"], "captured_at_invalid")
    age = (now_utc - captured_at).total_seconds()
    if age < -FUTURE_SKEW_SECONDS:
        raise AttentionError("snapshot_from_future")
    if age > max_age_seconds:
        raise AttentionError("snapshot_stale")

    start_bound, end_bound = _parse_window(
        window_start=window_start,
        window_end=window_end,
    )

    raw_cycles = snapshot["cycles"]
    if not isinstance(raw_cycles, list):
        raise AttentionError("cycles_invalid")
    if len(raw_cycles) > MAX_CYCLES:
        raise AttentionError("cycle_inventory_too_large")

    seen: set[tuple[str, str]] = set()
    cycles: list[dict[str, Any]] = []

    for raw in raw_cycles:
        if not isinstance(raw, dict) or set(raw) != CYCLE_KEYS:
            raise AttentionError("cycle_keys_invalid")

        worker_id = _token(raw["worker_id"], "worker_id_invalid")
        cycle_id = _token(raw["cycle_id"], "cycle_id_invalid")
        identity = (worker_id, cycle_id)
        if identity in seen:
            raise AttentionError("cycle_duplicate")
        seen.add(identity)

        started_at = _timestamp(raw["started_at"], "cycle_started_at_invalid")
        ended_at = _timestamp(raw["ended_at"], "cycle_ended_at_invalid")
        if ended_at < started_at:
            raise AttentionError("cycle_time_order_invalid")
        if ended_at > captured_at:
            raise AttentionError("cycle_after_capture")
        duration_seconds = int((ended_at - started_at).total_seconds())
        if duration_seconds > MAX_CYCLE_SECONDS:
            raise AttentionError("cycle_duration_exceeded")

        category = raw["category"]
        if not isinstance(category, str) or category not in CATEGORIES:
            raise AttentionError("cycle_category_invalid")

        vps_delegable = raw["vps_delegable"]
        if type(vps_delegable) is not bool:
            raise AttentionError("vps_delegable_invalid")
        if vps_delegable and category not in VPS_DELEGABLE_CATEGORIES:
            raise AttentionError("vps_delegable_category_invalid")

        outputs = raw["material_outputs"]
        if not isinstance(outputs, list) or len(outputs) > MAX_MATERIAL_OUTPUTS:
            raise AttentionError("material_outputs_invalid")
        if len(outputs) != len(set(outputs)):
            raise AttentionError("material_outputs_duplicate")
        for output in outputs:
            if not isinstance(output, str) or output not in MATERIAL_OUTPUTS:
                raise AttentionError("material_output_invalid")

        if start_bound and ended_at < start_bound:
            continue
        if end_bound and started_at > end_bound:
            continue

        useful = category in USEFUL_CATEGORIES or bool(USEFUL_OUTPUTS.intersection(outputs))
        cycles.append(
            {
                "worker_id": worker_id,
                "cycle_id": cycle_id,
                "started_at": started_at.isoformat(),
                "ended_at": ended_at.isoformat(),
                "duration_seconds": duration_seconds,
                "category": category,
                "useful_attention": useful,
                "vps_delegable": vps_delegable,
                "routing_advice": (
                    "delegate_and_continue" if vps_delegable else "retain_worker_attention"
                ),
                "material_outputs": sorted(outputs),
            }
        )

    cycles.sort(key=lambda item: (item["started_at"], item["worker_id"], item["cycle_id"]))

    worker_totals: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "cycle_count": 0,
            "attention_seconds": 0,
            "useful_attention_seconds": 0,
            "validation_attention_seconds": 0,
            "vps_delegable_attention_seconds": 0,
            "by_category": Counter(),
            "material_outputs": Counter(),
        }
    )

    for cycle in cycles:
        bucket = worker_totals[cycle["worker_id"]]
        bucket["cycle_count"] += 1
        bucket["attention_seconds"] += cycle["duration_seconds"]
        if cycle["useful_attention"]:
            bucket["useful_attention_seconds"] += cycle["duration_seconds"]
        if cycle["category"] == "validation":
            bucket["validation_attention_seconds"] += cycle["duration_seconds"]
        if cycle["vps_delegable"]:
            bucket["vps_delegable_attention_seconds"] += cycle["duration_seconds"]
        bucket["by_category"][cycle["category"]] += 1
        bucket["material_outputs"].update(cycle["material_outputs"])

    workers: list[dict[str, Any]] = []
    for worker_id in sorted(worker_totals):
        bucket = worker_totals[worker_id]
        total = bucket["attention_seconds"]
        workers.append(
            {
                "worker_id": worker_id,
                "cycle_count": bucket["cycle_count"],
                "attention_seconds": total,
                "useful_attention_seconds": bucket["useful_attention_seconds"],
                "useful_attention_ratio": _ratio(
                    bucket["useful_attention_seconds"], total
                ),
                "validation_attention_seconds": bucket["validation_attention_seconds"],
                "validation_attention_percentage": _percentage(
                    bucket["validation_attention_seconds"], total
                ),
                "vps_delegable_attention_seconds": bucket[
                    "vps_delegable_attention_seconds"
                ],
                "vps_delegable_attention_percentage": _percentage(
                    bucket["vps_delegable_attention_seconds"], total
                ),
                "by_category": {
                    category: bucket["by_category"].get(category, 0)
                    for category in CATEGORIES
                },
                "material_outputs": {
                    output: bucket["material_outputs"].get(output, 0)
                    for output in sorted(MATERIAL_OUTPUTS)
                },
            }
        )

    total_seconds = sum(item["attention_seconds"] for item in workers)
    useful_seconds = sum(item["useful_attention_seconds"] for item in workers)
    validation_seconds = sum(item["validation_attention_seconds"] for item in workers)
    delegable_seconds = sum(item["vps_delegable_attention_seconds"] for item in workers)

    return {
        "ok": True,
        "policy": POLICY,
        "captured_at": captured_at.isoformat(),
        "window": {
            "start": start_bound.isoformat() if start_bound else None,
            "end": end_bound.isoformat() if end_bound else None,
        },
        "cycle_count": len(cycles),
        "worker_count": len(workers),
        "attention_seconds": total_seconds,
        "useful_attention_seconds": useful_seconds,
        "useful_attention_ratio": _ratio(useful_seconds, total_seconds),
        "validation_attention_seconds": validation_seconds,
        "validation_attention_percentage": _percentage(
            validation_seconds, total_seconds
        ),
        "vps_delegable_attention_seconds": delegable_seconds,
        "vps_delegable_attention_percentage": _percentage(
            delegable_seconds, total_seconds
        ),
        "workers": workers,
        "cycles": cycles,
        "mutation_performed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--max-age-seconds", type=int, default=86400)
    parser.add_argument("--window-start")
    parser.add_argument("--window-end")
    parser.add_argument("--require-cycles", action="store_true")
    args = parser.parse_args(argv)

    try:
        payload = project_snapshot(
            load_snapshot(args.snapshot),
            max_age_seconds=args.max_age_seconds,
            window_start=args.window_start,
            window_end=args.window_end,
        )
    except AttentionError as exc:
        payload = {
            "ok": False,
            "policy": POLICY,
            "status": "incomplete",
            "errors": [exc.code],
            "mutation_performed": False,
        }
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        return 2

    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    if args.require_cycles and payload["cycle_count"] == 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

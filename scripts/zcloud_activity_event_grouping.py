#!/usr/bin/env python3
"""Group duplicate sanitized zCloud activity events by deterministic fingerprint."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from scripts import zcloud_project_activity_timeline as timeline

SCHEMA_VERSION = 1
MAX_GROUP_SECONDS = 3600

FINGERPRINT_FIELDS = (
    "project_id",
    "source",
    "category",
    "code",
    "worker_slot",
    "reason_code",
    "state",
)


def _dt(value: object) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


def semantic_fingerprint(event: dict) -> str:
    semantic = {
        key: event[key]
        for key in FINGERPRINT_FIELDS
        if key in event and event[key] is not None
    }
    encoded = json.dumps(semantic, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def group_events(events: list[dict], group_seconds: int = 60) -> list[dict]:
    group_seconds = int(group_seconds)
    if not (1 <= group_seconds <= MAX_GROUP_SECONDS):
        raise ValueError(f"group_seconds must be between 1 and {MAX_GROUP_SECONDS}")

    ordered = sorted(events, key=lambda item: _dt(item["ts"]))
    groups: list[dict] = []
    latest_by_fingerprint: dict[str, int] = {}

    for event in ordered:
        fingerprint = semantic_fingerprint(event)
        event_ts = _dt(event["ts"])
        prior_index = latest_by_fingerprint.get(fingerprint)
        prior = groups[prior_index] if prior_index is not None else None
        if prior is not None:
            gap = (event_ts - _dt(prior["last_at"])).total_seconds()
        else:
            gap = None

        if prior is not None and 0 <= float(gap) <= group_seconds:
            prior["count"] += 1
            prior["last_at"] = event["ts"]
            latest_by_fingerprint[fingerprint] = prior_index
            continue

        grouped = {
            "fingerprint": fingerprint,
            "count": 1,
            "first_at": event["ts"],
            "last_at": event["ts"],
            "project_id": event["project_id"],
            "source": event["source"],
            "category": event["category"],
            "code": event["code"],
            "summary": event["summary"],
        }
        for key in ("worker_slot", "reason_code", "state"):
            if key in event:
                grouped[key] = event[key]
        groups.append(grouped)
        latest_by_fingerprint[fingerprint] = len(groups) - 1

    groups.sort(key=lambda item: _dt(item["last_at"]), reverse=True)
    return groups


def report(
    db: Path,
    hours: float = 24.0,
    project: str | None = None,
    raw_limit: int = 200,
    group_seconds: int = 60,
    now: datetime | None = None,
) -> dict:
    source = timeline.report(db, hours, project, raw_limit, now)
    groups = group_events(source["timeline"], group_seconds)
    duplicates_collapsed = sum(max(0, int(group["count"]) - 1) for group in groups)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": source["generated_at"],
        "window_hours": source["window_hours"],
        "project_id": source["project_id"],
        "group_seconds": int(group_seconds),
        "source_event_count": len(source["timeline"]),
        "group_count": len(groups),
        "duplicates_collapsed": duplicates_collapsed,
        "method": "deterministic SHA-256 semantic fingerprint over sanitized timeline fields; duplicates group only within the bounded time window",
        "groups": groups,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Group duplicate zCloud activity events")
    parser.add_argument("--db", type=Path, default=Path(__file__).resolve().parents[1] / "history.db")
    parser.add_argument("--hours", type=float, default=24.0)
    parser.add_argument("--project")
    parser.add_argument("--raw-limit", type=int, default=200)
    parser.add_argument("--group-seconds", type=int, default=60)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    payload = report(args.db, args.hours, args.project, args.raw_limit, args.group_seconds)
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

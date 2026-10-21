#!/usr/bin/env python3
"""Fail-closed selector for retired zCloud conversation bindings.

This module never clears a conversation id, deletes a worker row, closes a
browser tab, or mutates SQLite. It classifies bounded tombstone evidence into
cleanup candidates or blocked entries so a later mutation lane can revalidate
all liveness references immediately before any write.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = "zcloud-dead-conversation-selector-v1"
MAX_INPUT_BYTES = 128 * 1024
MAX_RECORDS = 256
MAX_PROJECT_ID = 64
MAX_CONVERSATION_ID = 160
MAX_WORKER_SLOT = 64
MAX_REFERENCE_COUNT = 1024
MIN_TOMBSTONE_AGE_DAYS = 30
PROVIDERS = ("chatgpt", "claude")
TOMBSTONE_REASONS = (
    "conversation_reset_confirmed",
    "project_removed",
    "worker_slot_retired",
)
ALLOWED_KEYS = {
    "project_id",
    "worker_slot",
    "provider",
    "conversation_id",
    "tombstone_reason",
    "tombstoned_at",
    "project_present",
    "target_active",
    "worker_allocated",
    "active_claim",
    "pending_command",
    "browser_session_bound",
    "recovery_pending",
    "replacement_handoff_pending",
    "reference_count",
    "durable_tombstone_receipt",
}
PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
CONVERSATION_RE = re.compile(r"^[A-Za-z0-9_-]{8,160}$")


class InputError(ValueError):
    pass


def _strict_bool(value: Any, field: str) -> bool:
    if type(value) is not bool:
        raise InputError(f"{field}: expected boolean")
    return value


def _strict_int(value: Any, field: str, *, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise InputError(f"{field}: expected integer in [{minimum}, {maximum}]")
    return value


def _parse_time(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or len(value) > 64:
        raise InputError(f"{field}: expected bounded RFC3339 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InputError(f"{field}: invalid timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise InputError(f"{field}: timezone required")
    return parsed.astimezone(timezone.utc)


def validate_record(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise InputError("conversation record must be an object")
    if set(raw) != ALLOWED_KEYS:
        raise InputError("conversation record fields do not match schema")

    project_id = raw["project_id"]
    if (
        not isinstance(project_id, str)
        or not 1 <= len(project_id) <= MAX_PROJECT_ID
        or not PROJECT_RE.fullmatch(project_id)
    ):
        raise InputError("project_id: invalid canonical project id")

    provider = raw["provider"]
    if not isinstance(provider, str) or provider not in PROVIDERS:
        raise InputError("provider: unsupported provider")

    conversation_id = raw["conversation_id"]
    if (
        not isinstance(conversation_id, str)
        or not 8 <= len(conversation_id) <= MAX_CONVERSATION_ID
        or not CONVERSATION_RE.fullmatch(conversation_id)
    ):
        raise InputError("conversation_id: invalid bounded identifier")

    tombstone_reason = raw["tombstone_reason"]
    if not isinstance(tombstone_reason, str) or tombstone_reason not in TOMBSTONE_REASONS:
        raise InputError("tombstone_reason: unsupported retirement reason")

    return {
        "project_id": project_id,
        "worker_slot": _strict_int(
            raw["worker_slot"], "worker_slot", minimum=1, maximum=MAX_WORKER_SLOT
        ),
        "provider": provider,
        "conversation_id": conversation_id,
        "tombstone_reason": tombstone_reason,
        "tombstoned_at": _parse_time(raw["tombstoned_at"], "tombstoned_at"),
        "project_present": _strict_bool(raw["project_present"], "project_present"),
        "target_active": _strict_bool(raw["target_active"], "target_active"),
        "worker_allocated": _strict_bool(raw["worker_allocated"], "worker_allocated"),
        "active_claim": _strict_bool(raw["active_claim"], "active_claim"),
        "pending_command": _strict_bool(raw["pending_command"], "pending_command"),
        "browser_session_bound": _strict_bool(
            raw["browser_session_bound"], "browser_session_bound"
        ),
        "recovery_pending": _strict_bool(raw["recovery_pending"], "recovery_pending"),
        "replacement_handoff_pending": _strict_bool(
            raw["replacement_handoff_pending"], "replacement_handoff_pending"
        ),
        "reference_count": _strict_int(
            raw["reference_count"],
            "reference_count",
            minimum=0,
            maximum=MAX_REFERENCE_COUNT,
        ),
        "durable_tombstone_receipt": _strict_bool(
            raw["durable_tombstone_receipt"], "durable_tombstone_receipt"
        ),
    }


def classify(
    record: dict[str, Any],
    *,
    now: datetime,
    duplicate_binding: bool = False,
) -> tuple[str, list[str], int]:
    if now.tzinfo is None or now.utcoffset() is None:
        raise InputError("now: timezone required")
    now = now.astimezone(timezone.utc)
    age_seconds = (now - record["tombstoned_at"]).total_seconds()
    if age_seconds < 0:
        raise InputError("tombstoned_at: future timestamp")
    age_days = int(age_seconds // 86400)

    reasons: list[str] = []
    if duplicate_binding:
        reasons.append("DUPLICATE_BINDING")
    if age_days < MIN_TOMBSTONE_AGE_DAYS:
        reasons.append("TOMBSTONE_TOO_RECENT")
    if not record["durable_tombstone_receipt"]:
        reasons.append("NO_DURABLE_TOMBSTONE")
    if record["target_active"]:
        reasons.append("TARGET_ACTIVE")
    if record["worker_allocated"]:
        reasons.append("WORKER_ALLOCATED")
    if record["active_claim"]:
        reasons.append("ACTIVE_CLAIM")
    if record["pending_command"]:
        reasons.append("PENDING_COMMAND")
    if record["browser_session_bound"]:
        reasons.append("BROWSER_SESSION_BOUND")
    if record["recovery_pending"]:
        reasons.append("RECOVERY_PENDING")
    if record["replacement_handoff_pending"]:
        reasons.append("REPLACEMENT_HANDOFF_PENDING")
    if record["reference_count"]:
        reasons.append("REFERENCED")

    reason = record["tombstone_reason"]
    if reason == "project_removed" and record["project_present"]:
        reasons.append("PROJECT_STILL_PRESENT")
    if reason in {"conversation_reset_confirmed", "worker_slot_retired"} and not record[
        "project_present"
    ]:
        reasons.append("PROJECT_MISSING_FOR_LOCAL_RETIREMENT")

    return ("CANDIDATE" if not reasons else "BLOCKED", reasons, age_days)


def select(payload: Any, *, now: datetime) -> dict[str, Any]:
    if isinstance(payload, dict):
        if set(payload) != {"conversations"}:
            raise InputError("top-level object must contain only conversations")
        records = payload["conversations"]
    else:
        records = payload
    if not isinstance(records, list):
        raise InputError("input must be a list or object with a conversations list")
    if len(records) > MAX_RECORDS:
        raise InputError("too many conversation records")

    validated = [validate_record(raw) for raw in records]
    binding_counts = Counter(
        (record["provider"], record["conversation_id"]) for record in validated
    )

    candidates: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for record in validated:
        key = (record["provider"], record["conversation_id"])
        decision, reasons, age_days = classify(
            record,
            now=now,
            duplicate_binding=binding_counts[key] > 1,
        )
        binding_fingerprint = hashlib.sha256(
            f'{record["provider"]}:{record["conversation_id"]}'.encode("utf-8")
        ).hexdigest()
        item = {
            "project_id": record["project_id"],
            "worker_slot": record["worker_slot"],
            "provider": record["provider"],
            "binding_fingerprint": binding_fingerprint,
            "tombstone_reason": record["tombstone_reason"],
            "age_days": age_days,
        }
        if decision == "CANDIDATE":
            candidates.append(item)
        else:
            blocked.append({**item, "reasons": reasons})

    candidates.sort(
        key=lambda item: (
            -item["age_days"],
            item["project_id"],
            item["worker_slot"],
            item["provider"],
            item["binding_fingerprint"],
        )
    )
    blocked.sort(
        key=lambda item: (
            item["project_id"],
            item["worker_slot"],
            item["provider"],
            item["binding_fingerprint"],
        )
    )
    return {
        "schema": SCHEMA,
        "candidate_count": len(candidates),
        "blocked_count": len(blocked),
        "min_tombstone_age_days": MIN_TOMBSTONE_AGE_DAYS,
        "tombstone_reasons": list(TOMBSTONE_REASONS),
        "candidates": candidates,
        "blocked": blocked,
        "mutation_performed": False,
    }


def load_payload(path: Path) -> Any:
    if path.is_symlink():
        raise InputError("input path must not be a symlink")
    if not path.is_file():
        raise InputError("input path must be a regular file")
    size = path.stat().st_size
    if size > MAX_INPUT_BYTES:
        raise InputError("input file too large")
    data = path.read_bytes()
    if len(data) > MAX_INPUT_BYTES:
        raise InputError("input file too large")
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InputError("input must be valid UTF-8 JSON") from exc


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--now", required=True, help="timezone-aware RFC3339 timestamp")
    parser.add_argument("--require-candidates", action="store_true")
    args = parser.parse_args()

    try:
        result = select(load_payload(args.input), now=_parse_time(args.now, "now"))
    except InputError as exc:
        print(json.dumps({"schema": SCHEMA, "error": str(exc)}, sort_keys=True))
        return 2

    print(json.dumps(result, sort_keys=True))
    if args.require_candidates and not result["candidate_count"]:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

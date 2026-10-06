#!/usr/bin/env python3
"""Fail-closed selector for stale zCloud branches.

This module never deletes branches. It only classifies bounded metadata into
cleanup candidates or blocked entries so a later mutation lane can require a
separate approval/claim and exact-SHA recheck.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_INPUT_BYTES = 128 * 1024
MAX_BRANCHES = 256
MAX_NAME = 255
MIN_AGE_DAYS = 30
SAFE_PREFIXES = (
    "audit/",
    "canary/",
    "cleanup/",
    "debug/",
    "diagnostic/",
    "diagnostics/",
    "temp/",
    "tmp/",
)
ALLOWED_KEYS = {
    "name",
    "sha",
    "protected",
    "default_branch",
    "open_prs",
    "active_claim",
    "active_issue",
    "merged_to_default",
    "last_commit_at",
}
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
NAME_RE = re.compile(r"^[A-Za-z0-9._/-]+$")


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
        raise InputError("branch record must be an object")
    unknown = set(raw) - ALLOWED_KEYS
    if unknown:
        raise InputError("branch record contains unsupported fields")

    missing = ALLOWED_KEYS - set(raw)
    if missing:
        raise InputError("branch record missing required fields")

    name = raw["name"]
    if (
        not isinstance(name, str)
        or not 1 <= len(name) <= MAX_NAME
        or not NAME_RE.fullmatch(name)
        or ".." in name.split("/")
        or name.startswith("/")
        or name.endswith("/")
        or "//" in name
    ):
        raise InputError("name: invalid branch name")

    sha = raw["sha"]
    if not isinstance(sha, str) or not SHA_RE.fullmatch(sha):
        raise InputError("sha: expected lowercase 40-hex commit id")

    return {
        "name": name,
        "sha": sha,
        "protected": _strict_bool(raw["protected"], "protected"),
        "default_branch": _strict_bool(raw["default_branch"], "default_branch"),
        "open_prs": _strict_int(raw["open_prs"], "open_prs", minimum=0, maximum=100),
        "active_claim": _strict_bool(raw["active_claim"], "active_claim"),
        "active_issue": _strict_bool(raw["active_issue"], "active_issue"),
        "merged_to_default": _strict_bool(raw["merged_to_default"], "merged_to_default"),
        "last_commit_at": _parse_time(raw["last_commit_at"], "last_commit_at"),
    }


def classify(record: dict[str, Any], *, now: datetime) -> tuple[str, list[str], int]:
    if now.tzinfo is None or now.utcoffset() is None:
        raise InputError("now: timezone required")
    now = now.astimezone(timezone.utc)
    age_seconds = (now - record["last_commit_at"]).total_seconds()
    if age_seconds < 0:
        raise InputError("last_commit_at: future timestamp")
    age_days = int(age_seconds // 86400)

    reasons: list[str] = []
    if record["default_branch"]:
        reasons.append("DEFAULT_BRANCH")
    if record["protected"]:
        reasons.append("PROTECTED")
    if record["open_prs"]:
        reasons.append("OPEN_PR")
    if record["active_claim"]:
        reasons.append("ACTIVE_CLAIM")
    if record["active_issue"]:
        reasons.append("ACTIVE_ISSUE")
    if not record["merged_to_default"]:
        reasons.append("NOT_MERGED_TO_DEFAULT")
    if age_days < MIN_AGE_DAYS:
        reasons.append("TOO_RECENT")
    if not record["name"].startswith(SAFE_PREFIXES):
        reasons.append("UNSAFE_NAMESPACE")

    return ("CANDIDATE" if not reasons else "BLOCKED", reasons, age_days)


def select(payload: Any, *, now: datetime) -> dict[str, Any]:
    records = payload.get("branches") if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        raise InputError("input must be a list or object with a branches list")
    if len(records) > MAX_BRANCHES:
        raise InputError("too many branch records")

    candidates = []
    blocked = []
    for raw in records:
        record = validate_record(raw)
        decision, reasons, age_days = classify(record, now=now)
        item = {
            "name": record["name"],
            "sha": record["sha"],
            "age_days": age_days,
        }
        if decision == "CANDIDATE":
            candidates.append(item)
        else:
            blocked.append({**item, "reasons": reasons})

    candidates.sort(key=lambda item: (-item["age_days"], item["name"]))
    blocked.sort(key=lambda item: item["name"])
    return {
        "schema": "zcloud-stale-branch-selector-v1",
        "candidate_count": len(candidates),
        "blocked_count": len(blocked),
        "min_age_days": MIN_AGE_DAYS,
        "safe_prefixes": list(SAFE_PREFIXES),
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
        now = _parse_time(args.now, "now")
        result = select(load_payload(args.input), now=now)
    except InputError as exc:
        print(json.dumps({"schema": "zcloud-stale-branch-selector-v1", "error": str(exc)}, sort_keys=True))
        return 2

    print(json.dumps(result, sort_keys=True))
    if args.require_candidates and not result["candidate_count"]:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

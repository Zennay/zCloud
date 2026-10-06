#!/usr/bin/env python3
"""Fail-closed selector for stale temporary zCloud evidence artifacts.

The selector is intentionally read-only. It consumes bounded metadata from a
separate inventory step and never unlinks, truncates, moves, or rewrites files.
A later cleanup lane must revalidate filesystem identity and ownership before
performing any mutation.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = "zcloud-temp-artifact-selector-v1"
MAX_INPUT_BYTES = 128 * 1024
MAX_RECORDS = 512
MAX_NAME = 180
MAX_SIZE_BYTES = 128 * 1024 * 1024
MAX_CANDIDATES = 50
MAX_CANDIDATE_BYTES = 512 * 1024 * 1024
MIN_AGE_DAYS = 7
SAFE_SCOPES = ("system_tmp", "runner_temp")
SAFE_PREFIXES = (
    "zcloud-",
    "ftmo-",
    "haxlab-",
    "lightup-",
    "raiseai-",
    "supa-",
    "ulab-",
    "zssh-",
    "zguard-",
)
SAFE_SUFFIXES = (".json", ".txt", ".log")
NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
ALLOWED_KEYS = {
    "scope",
    "name",
    "size_bytes",
    "modified_at",
    "regular_file",
    "symlink",
    "open_handles",
    "active_process_ref",
    "active_run_ref",
    "durable_copy",
}


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
        raise InputError("artifact record must be an object")
    if set(raw) != ALLOWED_KEYS:
        raise InputError("artifact record fields do not match schema")

    scope = raw["scope"]
    if not isinstance(scope, str) or scope not in SAFE_SCOPES:
        raise InputError("scope: unsupported temporary root")

    name = raw["name"]
    if (
        not isinstance(name, str)
        or not 1 <= len(name) <= MAX_NAME
        or not NAME_RE.fullmatch(name)
        or name in {".", ".."}
    ):
        raise InputError("name: invalid single-file artifact name")

    size_bytes = _strict_int(
        raw["size_bytes"], "size_bytes", minimum=0, maximum=MAX_SIZE_BYTES
    )
    open_handles = _strict_int(raw["open_handles"], "open_handles", minimum=0, maximum=1024)

    return {
        "scope": scope,
        "name": name,
        "size_bytes": size_bytes,
        "modified_at": _parse_time(raw["modified_at"], "modified_at"),
        "regular_file": _strict_bool(raw["regular_file"], "regular_file"),
        "symlink": _strict_bool(raw["symlink"], "symlink"),
        "open_handles": open_handles,
        "active_process_ref": _strict_bool(raw["active_process_ref"], "active_process_ref"),
        "active_run_ref": _strict_bool(raw["active_run_ref"], "active_run_ref"),
        "durable_copy": _strict_bool(raw["durable_copy"], "durable_copy"),
    }


def classify(record: dict[str, Any], *, now: datetime) -> tuple[str, list[str], int]:
    if now.tzinfo is None or now.utcoffset() is None:
        raise InputError("now: timezone required")
    now = now.astimezone(timezone.utc)
    age_seconds = (now - record["modified_at"]).total_seconds()
    if age_seconds < 0:
        raise InputError("modified_at: future timestamp")
    age_days = int(age_seconds // 86400)

    reasons: list[str] = []
    if not record["regular_file"]:
        reasons.append("NOT_REGULAR_FILE")
    if record["symlink"]:
        reasons.append("SYMLINK")
    if record["open_handles"]:
        reasons.append("OPEN_HANDLE")
    if record["active_process_ref"]:
        reasons.append("ACTIVE_PROCESS_REF")
    if record["active_run_ref"]:
        reasons.append("ACTIVE_RUN_REF")
    if not record["durable_copy"]:
        reasons.append("NO_DURABLE_COPY")
    if age_days < MIN_AGE_DAYS:
        reasons.append("TOO_RECENT")
    if not record["name"].startswith(SAFE_PREFIXES):
        reasons.append("UNSAFE_PREFIX")
    if not record["name"].endswith(SAFE_SUFFIXES):
        reasons.append("UNSAFE_SUFFIX")

    return ("CANDIDATE" if not reasons else "BLOCKED", reasons, age_days)


def select(payload: Any, *, now: datetime) -> dict[str, Any]:
    if isinstance(payload, dict):
        if set(payload) != {"artifacts"}:
            raise InputError("top-level object must contain only artifacts")
        records = payload["artifacts"]
    else:
        records = payload
    if not isinstance(records, list):
        raise InputError("input must be a list or object with an artifacts list")
    if len(records) > MAX_RECORDS:
        raise InputError("too many artifact records")

    eligible: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for raw in records:
        record = validate_record(raw)
        decision, reasons, age_days = classify(record, now=now)
        item = {
            "scope": record["scope"],
            "name": record["name"],
            "age_days": age_days,
            "size_bytes": record["size_bytes"],
        }
        if decision == "CANDIDATE":
            eligible.append(item)
        else:
            blocked.append({**item, "reasons": reasons})

    eligible.sort(key=lambda item: (-item["age_days"], item["scope"], item["name"]))
    candidates: list[dict[str, Any]] = []
    selected_bytes = 0
    for item in eligible:
        reasons: list[str] = []
        if len(candidates) >= MAX_CANDIDATES:
            reasons.append("CANDIDATE_COUNT_BUDGET")
        if selected_bytes + item["size_bytes"] > MAX_CANDIDATE_BYTES:
            reasons.append("CANDIDATE_BYTE_BUDGET")
        if reasons:
            blocked.append({**item, "reasons": reasons})
            continue
        candidates.append(item)
        selected_bytes += item["size_bytes"]

    blocked.sort(key=lambda item: (item["scope"], item["name"]))
    return {
        "schema": SCHEMA,
        "candidate_count": len(candidates),
        "candidate_bytes": selected_bytes,
        "blocked_count": len(blocked),
        "min_age_days": MIN_AGE_DAYS,
        "max_candidates": MAX_CANDIDATES,
        "max_candidate_bytes": MAX_CANDIDATE_BYTES,
        "safe_scopes": list(SAFE_SCOPES),
        "safe_prefixes": list(SAFE_PREFIXES),
        "safe_suffixes": list(SAFE_SUFFIXES),
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

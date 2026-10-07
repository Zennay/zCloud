#!/usr/bin/env python3
"""Classify whether a serialized zCloud writer window is safely released."""

from __future__ import annotations

import argparse
import json
import re
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_BYTES = 64 * 1024
MAX_OWNERS = 32
DEFAULT_MAX_AGE_SECONDS = 15 * 60
MAX_FUTURE_SKEW_SECONDS = 60

PR_REF_RE = re.compile(r"^pr:[1-9][0-9]{0,9}$")
ISSUE_REF_RE = re.compile(r"^issue:[1-9][0-9]{0,9}$")
WORKER_REF_RE = re.compile(r"^worker:[a-z0-9][a-z0-9._:-]{0,63}$")

SOURCE_STATES = {
    "pull_request": {"open", "closed", "merged"},
    "issue": {"open", "closed"},
    "worker": {"active", "released"},
}
ACTIVE_STATES = {
    "pull_request": {"open"},
    "issue": {"open"},
    "worker": {"active"},
}


class WindowEvidenceError(ValueError):
    """Raised when serialized-window evidence is malformed or stale."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or len(value) > 40:
        raise WindowEvidenceError("observed_at_invalid")
    raw = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise WindowEvidenceError("observed_at_invalid") from exc
    if parsed.tzinfo is None:
        raise WindowEvidenceError("observed_at_timezone_missing")
    return parsed.astimezone(timezone.utc)


def _owner_ref(kind: str, value: Any) -> str:
    if not isinstance(value, str):
        raise WindowEvidenceError("owner_ref_invalid")
    matcher = {
        "pull_request": PR_REF_RE,
        "issue": ISSUE_REF_RE,
        "worker": WORKER_REF_RE,
    }[kind]
    if not matcher.fullmatch(value):
        raise WindowEvidenceError("owner_ref_invalid")
    return value


def load_snapshot(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise WindowEvidenceError("snapshot_symlink_rejected")
    try:
        metadata = path.stat()
    except OSError as exc:
        raise WindowEvidenceError("snapshot_unreadable") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise WindowEvidenceError("snapshot_not_regular")
    if metadata.st_size > MAX_BYTES:
        raise WindowEvidenceError("snapshot_too_large")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise WindowEvidenceError("snapshot_unreadable") from exc
    if len(raw) > MAX_BYTES:
        raise WindowEvidenceError("snapshot_too_large")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise WindowEvidenceError("snapshot_invalid_json") from exc
    if not isinstance(data, dict):
        raise WindowEvidenceError("snapshot_not_object")
    return data


def audit_snapshot(
    snapshot: dict[str, Any],
    *,
    now: datetime | None = None,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
) -> dict[str, Any]:
    if type(max_age_seconds) is not int or not 1 <= max_age_seconds <= 3600:
        raise WindowEvidenceError("max_age_invalid")
    if set(snapshot) != {"schema_version", "observed_at", "window_id", "owners"}:
        raise WindowEvidenceError("snapshot_keys_invalid")
    if type(snapshot["schema_version"]) is not int or snapshot["schema_version"] != 1:
        raise WindowEvidenceError("schema_version_unsupported")
    window_id = snapshot["window_id"]
    if (
        not isinstance(window_id, str)
        or not re.fullmatch(r"^[a-z0-9][a-z0-9._:-]{0,79}$", window_id)
    ):
        raise WindowEvidenceError("window_id_invalid")

    observed_at = _timestamp(snapshot["observed_at"])
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    age_seconds = (current - observed_at).total_seconds()
    if age_seconds < -MAX_FUTURE_SKEW_SECONDS:
        raise WindowEvidenceError("snapshot_from_future")
    if age_seconds > max_age_seconds:
        raise WindowEvidenceError("snapshot_stale")

    owners = snapshot["owners"]
    if not isinstance(owners, list) or not 1 <= len(owners) <= MAX_OWNERS:
        raise WindowEvidenceError("owners_invalid")

    seen: set[str] = set()
    active_refs: list[str] = []
    unproven_release_refs: list[str] = []
    released_refs: list[str] = []

    for row in owners:
        if not isinstance(row, dict) or set(row) != {
            "kind",
            "ref",
            "state",
            "release_proven",
        }:
            raise WindowEvidenceError("owner_invalid")
        kind = row["kind"]
        if not isinstance(kind, str) or kind not in SOURCE_STATES:
            raise WindowEvidenceError("owner_kind_invalid")
        state_value = row["state"]
        if not isinstance(state_value, str) or state_value not in SOURCE_STATES[kind]:
            raise WindowEvidenceError("owner_state_invalid")
        release_proven = row["release_proven"]
        if not isinstance(release_proven, bool):
            raise WindowEvidenceError("owner_release_proven_invalid")
        ref = _owner_ref(kind, row["ref"])
        if ref in seen:
            raise WindowEvidenceError("owner_duplicate")
        seen.add(ref)

        active = state_value in ACTIVE_STATES[kind]
        if active:
            if release_proven:
                raise WindowEvidenceError("active_owner_marked_released")
            active_refs.append(ref)
        elif release_proven:
            released_refs.append(ref)
        else:
            unproven_release_refs.append(ref)

    active_refs.sort()
    unproven_release_refs.sort()
    released_refs.sort()

    if active_refs:
        status_value = "blocked"
    elif unproven_release_refs:
        status_value = "incomplete"
    else:
        status_value = "clear"

    return {
        "ok": status_value == "clear",
        "schema_version": 1,
        "window_id": window_id,
        "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
        "status": status_value,
        "owner_count": len(owners),
        "active_owner_refs": active_refs,
        "unproven_release_refs": unproven_release_refs,
        "released_owner_refs": released_refs,
        "mutation_performed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit bounded evidence for a serialized zCloud writer window"
    )
    parser.add_argument("snapshot", type=Path)
    parser.add_argument(
        "--max-age-seconds",
        type=int,
        default=DEFAULT_MAX_AGE_SECONDS,
    )
    parser.add_argument("--require-clear", action="store_true")
    args = parser.parse_args(argv)

    try:
        result = audit_snapshot(
            load_snapshot(args.snapshot),
            max_age_seconds=args.max_age_seconds,
        )
    except WindowEvidenceError as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "status": "incomplete",
                    "errors": [exc.code],
                    "mutation_performed": False,
                },
                sort_keys=True,
            )
        )
        return 1

    print(json.dumps(result, sort_keys=True))
    if args.require_clear and result["status"] != "clear":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

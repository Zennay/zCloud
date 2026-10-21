#!/usr/bin/env python3
"""Audit bounded roadmap capability ownership without reading or mutating runtime state."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_BYTES = 256 * 1024
MAX_CAPABILITIES = 200
MAX_OWNERS = 500
DEFAULT_MAX_AGE_SECONDS = 15 * 60
MAX_FUTURE_SKEW_SECONDS = 60

TOKEN_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,79}$")
OWNER_KIND = {"pr", "issue", "worker"}
OWNER_STATE = {"active", "review_ready", "waiting_dependency", "closed"}
CAPABILITY_STATE = {"open", "complete", "cancelled"}
ACTIVE_OWNER_STATES = {"active", "review_ready", "waiting_dependency"}


class SnapshotError(ValueError):
    """Raised for malformed or stale ownership evidence."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _token(value: Any, code: str) -> str:
    if not isinstance(value, str) or not TOKEN_RE.fullmatch(value):
        raise SnapshotError(code)
    return value


def _bool(value: Any, code: str) -> bool:
    if not isinstance(value, bool):
        raise SnapshotError(code)
    return value


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or len(value) > 40:
        raise SnapshotError("observed_at_invalid")
    raw = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise SnapshotError("observed_at_invalid") from exc
    if parsed.tzinfo is None:
        raise SnapshotError("observed_at_timezone_missing")
    return parsed.astimezone(timezone.utc)


def load_snapshot(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise SnapshotError("snapshot_symlink_rejected")
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise SnapshotError("snapshot_unreadable") from exc
    if size > MAX_BYTES:
        raise SnapshotError("snapshot_too_large")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SnapshotError("snapshot_invalid_json") from exc
    if not isinstance(data, dict):
        raise SnapshotError("snapshot_not_object")
    return data


def audit_snapshot(
    snapshot: dict[str, Any],
    *,
    now: datetime | None = None,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
) -> dict[str, Any]:
    if type(max_age_seconds) is not int or not 1 <= max_age_seconds <= 3600:
        raise SnapshotError("max_age_invalid")
    if set(snapshot) != {"schema_version", "observed_at", "lane", "capabilities", "owners"}:
        raise SnapshotError("snapshot_keys_invalid")
    if type(snapshot["schema_version"]) is not int or snapshot["schema_version"] != 1:
        raise SnapshotError("schema_version_unsupported")
    lane = _token(snapshot["lane"], "lane_invalid")
    observed_at = _timestamp(snapshot["observed_at"])
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    age = (current - observed_at).total_seconds()
    if age < -MAX_FUTURE_SKEW_SECONDS:
        raise SnapshotError("snapshot_from_future")
    if age > max_age_seconds:
        raise SnapshotError("snapshot_stale")

    capabilities = snapshot["capabilities"]
    owners = snapshot["owners"]
    if not isinstance(capabilities, list) or len(capabilities) > MAX_CAPABILITIES:
        raise SnapshotError("capabilities_invalid")
    if not isinstance(owners, list) or len(owners) > MAX_OWNERS:
        raise SnapshotError("owners_invalid")

    capability_map: dict[str, dict[str, Any]] = {}
    for row in capabilities:
        if not isinstance(row, dict) or set(row) != {
            "id",
            "lane",
            "state",
            "executable",
            "dependency_ready",
            "human_gate",
        }:
            raise SnapshotError("capability_invalid")
        capability_id = _token(row["id"], "capability_id_invalid")
        capability_lane = _token(row["lane"], "capability_lane_invalid")
        state = row["state"]
        if not isinstance(state, str) or state not in CAPABILITY_STATE:
            raise SnapshotError("capability_state_invalid")
        if capability_id in capability_map:
            raise SnapshotError("capability_duplicate")
        capability_map[capability_id] = {
            "lane": capability_lane,
            "state": state,
            "executable": _bool(row["executable"], "capability_executable_invalid"),
            "dependency_ready": _bool(
                row["dependency_ready"], "capability_dependency_ready_invalid"
            ),
            "human_gate": _bool(row["human_gate"], "capability_human_gate_invalid"),
        }

    active_owners: dict[str, list[str]] = {key: [] for key in capability_map}
    owner_identities: set[tuple[str, str, str]] = set()
    for row in owners:
        if not isinstance(row, dict) or set(row) != {
            "capability_id",
            "kind",
            "id",
            "state",
        }:
            raise SnapshotError("owner_invalid")
        capability_id = _token(row["capability_id"], "owner_capability_id_invalid")
        if capability_id not in capability_map:
            raise SnapshotError("owner_unknown_capability")
        kind = row["kind"]
        state = row["state"]
        owner_id = _token(row["id"], "owner_id_invalid")
        if not isinstance(kind, str) or kind not in OWNER_KIND:
            raise SnapshotError("owner_kind_invalid")
        if not isinstance(state, str) or state not in OWNER_STATE:
            raise SnapshotError("owner_state_invalid")
        identity = (capability_id, kind, owner_id)
        if identity in owner_identities:
            raise SnapshotError("owner_duplicate")
        owner_identities.add(identity)
        if state in ACTIVE_OWNER_STATES:
            active_owners[capability_id].append(f"{kind}:{owner_id}")

    eligible = sorted(
        capability_id
        for capability_id, row in capability_map.items()
        if row["lane"] == lane
        and row["state"] == "open"
        and row["executable"]
        and row["dependency_ready"]
        and not row["human_gate"]
    )
    candidates = [cid for cid in eligible if not active_owners[cid]]
    collisions = {
        cid: sorted(active_owners[cid])
        for cid in eligible
        if len(active_owners[cid]) > 1
    }
    owned = [cid for cid in eligible if len(active_owners[cid]) == 1]

    if collisions:
        state = "collision"
    elif candidates:
        state = "ready"
    elif eligible:
        state = "saturated"
    else:
        state = "idle"

    return {
        "ok": not collisions,
        "schema_version": 1,
        "lane": lane,
        "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
        "state": state,
        "eligible_capability_ids": eligible,
        "candidate_capability_ids": candidates,
        "owned_capability_ids": owned,
        "collisions": collisions,
        "counts": {
            "capabilities": len(capability_map),
            "owners": len(owners),
            "eligible": len(eligible),
            "candidates": len(candidates),
            "collisions": len(collisions),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit a bounded zCloud roadmap ownership snapshot"
    )
    parser.add_argument("snapshot", type=Path)
    parser.add_argument(
        "--max-age-seconds",
        type=int,
        default=DEFAULT_MAX_AGE_SECONDS,
    )
    parser.add_argument("--require-candidate", action="store_true")
    parser.add_argument("--require-clear", action="store_true")
    args = parser.parse_args(argv)

    try:
        result = audit_snapshot(
            load_snapshot(args.snapshot),
            max_age_seconds=args.max_age_seconds,
        )
    except SnapshotError as exc:
        print(
            json.dumps(
                {"ok": False, "state": "incomplete", "errors": [exc.code]},
                sort_keys=True,
            )
        )
        return 1

    print(json.dumps(result, sort_keys=True))
    if args.require_clear and result["state"] == "collision":
        return 2
    if args.require_candidate and result["state"] != "ready":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

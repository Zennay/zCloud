#!/usr/bin/env python3
"""Bounded, non-authorizing classifier for zCloud deploy safe-idle log receipts.

Consumes untrusted, operator-supplied GitHub job text through stdin.  It never
requests GitHub API tokens, discloses worker identities, writes a file, or acts
on a worker. One log receipt is insufficient to authorize deployment.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import re
import sys

MAX_LOG_BYTES = 256 * 1024
MAX_MARKER_BYTES = 24 * 1024
MARKER = "ZCLOUD_DEPLOY_SAFE_IDLE_BLOCKED: browser workers did not reach safe-idle before deployment timeout; "
LOG_LINE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z) " +
    re.escape(MARKER) + r"(\[[^\r\n]*\])$", re.MULTILINE
)
SQLITE_READONLY_MARKER = "ZCLOUD_DEPLOY_SAFE_IDLE_BLOCKED: attempt to write a readonly database"
SQLITE_READONLY_LINE = re.compile(
    r"^(\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(?:\\.\\d+)?Z) " +
    re.escape(SQLITE_READONLY_MARKER) + r"$", re.MULTILINE
)
_ALLOWED_EVENT = {"heartbeat", "generation-progress", "generation-started",
                  "generation-completed", "blocked", "ready", "idle"}


class UntrustedLog(ValueError):
    pass


def _no_duplicates(pairs):
    data = {}
    for name, value in pairs:
        if name in data:
            raise UntrustedLog("duplicate_json_key")
        data[name] = value
    return data


def _no_constant(_):
    raise UntrustedLog("nonfinite_json")


def _validated_marker_payload(raw: str) -> object:
    """Deny ambiguous JSON throughout the receipt, including ignored extras."""
    def finite_float(value: str) -> float:
        number = float(value)
        if not math.isfinite(number):
            raise UntrustedLog("nonfinite_json")
        return number

    try:
        value = json.loads(raw, object_pairs_hook=_no_duplicates,
                           parse_constant=_no_constant, parse_float=finite_float)
    except (ValueError, OverflowError, RecursionError) as exc:
        if isinstance(exc, UntrustedLog):
            raise
        raise UntrustedLog("invalid_blocker_json") from exc

    remaining = [4096]
    def scan(item: object, depth: int = 0) -> None:
        remaining[0] -= 1
        if remaining[0] < 0 or depth > 24:
            raise UntrustedLog("invalid_blocker_structure")
        if type(item) is str:
            if any(0xD800 <= ord(character) <= 0xDFFF for character in item):
                raise UntrustedLog("invalid_unicode")
        elif type(item) is dict:
            for key, nested in item.items():
                scan(key, depth + 1)
                scan(nested, depth + 1)
        elif type(item) is list:
            for nested in item:
                scan(nested, depth + 1)
        elif type(item) is float and not math.isfinite(item):
            raise UntrustedLog("nonfinite_json")
    scan(value)
    return value


def classify(text: str, now: datetime | None = None) -> dict:
    if type(text) is not str or len(text.encode("utf-8", errors="replace")) > MAX_LOG_BYTES:
        raise UntrustedLog("invalid_log_size")
    matches = LOG_LINE.findall(text)
    readonly = SQLITE_READONLY_LINE.findall(text)
    if len(matches) + len(readonly) != 1:
        # Mixed cause markers are ambiguous: never pick one opportunistically.
        raise UntrustedLog("ambiguous_or_missing_safe_idle_marker")
    stamp, payload = matches[0] if matches else (readonly[0], None)
    try:
        event_time = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise UntrustedLog("invalid_log_timestamp") from exc
    now = datetime.now(timezone.utc) if now is None else now
    if type(now) is not datetime or now.tzinfo is None:
        raise UntrustedLog("invalid_observation_clock")
    elapsed = (now - event_time).total_seconds()
    if elapsed < -5:
        raise UntrustedLog("future_log_timestamp")
    marker_age_seconds = max(0, int(elapsed))
    # Historic browser reports must not be mistaken for live worker state.
    fresh_for_triage = marker_age_seconds <= 900
    if readonly:
        # A SQLite exception is NOT a browser generation timeout. No worker
        # population is observed, so blocker_count must remain unknown.
        return {
            "schema": "zcloud-safe-idle-blocker-evidence-v1",
            "source": "untrusted_operator_supplied_log",
            "failure_category": "sqlite_readonly_error_marker",
            "blocker_count": None,
            "marker_age_seconds": marker_age_seconds,
            "fresh_for_triage": bool(fresh_for_triage),
            "independent_live_worker_state_verified": False,
            "classes": None,
            "only_acknowledged_draining_generation": False,
            "proves_browser_text_progress": False,
            "proves_process_stuck": False,
            "production_deploy_verified": False,
            "authorizes_worker_interrupt": False,
            "authorizes_mutation": False,
        }
    if len(payload.encode("utf-8")) > MAX_MARKER_BYTES:
        raise UntrustedLog("oversized_marker")
    rows = _validated_marker_payload(payload)
    if type(rows) is not list or not 1 <= len(rows) <= 32:
        raise UntrustedLog("invalid_blocker_count")
    categories = {"generating_after_acknowledged_drain": 0,
                  "draining_without_completed_acknowledgement": 0,
                  "other_active_blocker": 0}
    seen = set()
    for row in rows:
        if type(row) is not dict:
            raise UntrustedLog("invalid_blocker")
        worker = row.get("worker_id")
        if (type(worker) is not str or re.fullmatch(r"[a-z][a-z0-9_-]{0,39}::w[1-9][0-9]?", worker) is None
                or worker in seen):
            raise UntrustedLog("invalid_or_duplicate_worker_identity")
        seen.add(worker)
        if type(row.get("generating")) is not bool or type(row.get("sending")) is not bool:
            raise UntrustedLog("ambiguous_generation_flags")
        if row["generating"] is False and row["sending"] is False:
            raise UntrustedLog("idle_worker_claimed_as_blocker")
        desired = row.get("desired_state")
        if desired is not None and (type(desired) is not str or
                                    desired not in {"running", "draining", "paused", "stopped"}):
            raise UntrustedLog("invalid_desired_state")
        status = row.get("drain_command_status")
        if status is not None and (type(status) is not str or
                                   status not in {"pending", "completed", "failed",
                                                  "skipped", "running", "cancelled"}):
            raise UntrustedLog("invalid_drain_status")
        age = row.get("last_event_age_seconds")
        if age is not None and (type(age) not in (int, float) or not 0 <= age <= 3600):
            raise UntrustedLog("invalid_event_age")
        event = row.get("last_event")
        if event is not None and (type(event) is not str or event not in _ALLOWED_EVENT):
            raise UntrustedLog("unknown_event_type")
        command_id = row.get("drain_command_id")
        if command_id is not None and (type(command_id) is not int or command_id <= 0):
            raise UntrustedLog("invalid_command_id")
        if desired == "draining" and status == "completed" and row["generating"]:
            categories["generating_after_acknowledged_drain"] += 1
        elif desired == "draining" and status != "completed":
            categories["draining_without_completed_acknowledgement"] += 1
        else:
            categories["other_active_blocker"] += 1
    return {
        "schema": "zcloud-safe-idle-blocker-evidence-v1",
        "source": "untrusted_operator_supplied_log",
        "failure_category": "safe_idle_timeout_marker",
        "blocker_count": len(rows),
        "marker_age_seconds": marker_age_seconds,
        "fresh_for_triage": bool(fresh_for_triage),
        "independent_live_worker_state_verified": False,
        "classes": categories,
        "only_acknowledged_draining_generation": (
            categories["generating_after_acknowledged_drain"] == len(rows)
        ),
        "proves_browser_text_progress": False,
        "proves_process_stuck": False,
        "production_deploy_verified": False,
        "authorizes_worker_interrupt": False,
        "authorizes_mutation": False,
    }


def main() -> int:
    raw = sys.stdin.buffer.read(MAX_LOG_BYTES + 1)
    try:
        if len(raw) > MAX_LOG_BYTES:
            raise UntrustedLog("invalid_log_size")
        text = raw.decode("utf-8", errors="strict")
        report = classify(text)
    except (UntrustedLog, UnicodeError) as exc:
        # Never emit raw errors, source bytes, IDs or log paths.
        cause = str(exc)
        public = cause if cause in {
            "invalid_log_size", "ambiguous_or_missing_safe_idle_marker",
            "oversized_marker", "invalid_blocker_json", "invalid_blocker_count",
            "duplicate_json_key", "nonfinite_json", "invalid_blocker",
            "invalid_or_duplicate_worker_identity", "ambiguous_generation_flags",
            "idle_worker_claimed_as_blocker", "invalid_desired_state",
            "invalid_drain_status", "invalid_event_age", "unknown_event_type",
            "invalid_command_id", "invalid_log_timestamp", "future_log_timestamp",
            "invalid_observation_clock", "invalid_unicode", "invalid_blocker_structure"
        } else "invalid_log"
        print(json.dumps({"schema": "zcloud-safe-idle-blocker-evidence-v1",
                          "error": public, "authorizes_mutation": False,
                          "production_deploy_verified": False}))
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

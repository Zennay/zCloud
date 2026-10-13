#!/usr/bin/env python3
"""Bounded, non-authorizing classifier for zCloud deploy safe-idle log receipts.

Consumes untrusted, operator-supplied GitHub job text through stdin.  It never
requests GitHub API tokens, discloses worker identities, writes a file, or acts
on a worker. One log receipt is insufficient to authorize deployment.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import re
import sys

MAX_LOG_BYTES = 256 * 1024
MAX_MARKER_BYTES = 24 * 1024
MARKER = "ZCLOUD_DEPLOY_SAFE_IDLE_BLOCKED: browser workers did not reach safe-idle before deployment timeout; "
LOG_LINE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z) " +
    re.escape(MARKER) + r"(\[[^\r\n]*\])$", re.MULTILINE
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


def classify(text: str) -> dict:
    if type(text) is not str or len(text.encode("utf-8", errors="replace")) > MAX_LOG_BYTES:
        raise UntrustedLog("invalid_log_size")
    matches = LOG_LINE.findall(text)
    if len(matches) != 1:
        raise UntrustedLog("ambiguous_or_missing_safe_idle_marker")
    stamp, payload = matches[0]
    try:
        event_time = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise UntrustedLog("invalid_log_timestamp") from exc
    if (event_time - datetime.now(timezone.utc)).total_seconds() > 5:
        raise UntrustedLog("future_log_timestamp")
    if len(payload.encode("utf-8")) > MAX_MARKER_BYTES:
        raise UntrustedLog("oversized_marker")
    try:
        rows = json.loads(payload, object_pairs_hook=_no_duplicates,
                          parse_constant=_no_constant)
    except (json.JSONDecodeError, UnicodeError, RecursionError) as exc:
        raise UntrustedLog("invalid_blocker_json") from exc
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
        if desired is not None and desired not in {"running", "draining", "paused", "stopped"}:
            raise UntrustedLog("invalid_desired_state")
        status = row.get("drain_command_status")
        if status is not None and status not in {"pending", "completed", "failed",
                                                   "skipped", "running", "cancelled"}:
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
        "blocker_count": len(rows),
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
            "invalid_command_id", "invalid_log_timestamp", "future_log_timestamp"
        } else "invalid_log"
        print(json.dumps({"schema": "zcloud-safe-idle-blocker-evidence-v1",
                          "error": public, "authorizes_mutation": False,
                          "production_deploy_verified": False}))
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

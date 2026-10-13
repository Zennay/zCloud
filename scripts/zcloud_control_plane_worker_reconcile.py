#!/usr/bin/env python3
"""Read-only reconciliation of zCloud scheduler slots, worker targets and live evidence.

Operator diagnostic, NOT an admission or recovery tool. It never starts a tab,
dispatches a prompt, restarts a service, writes SQLite or authorizes a deploy.
The report intentionally excludes conversation IDs, prompts and command bodies.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

API = "http://127.0.0.1:8765/api/runner-live"
MAX_RESPONSE_BYTES = 256 * 1024
MAX_SLOTS = 128


class InvalidEvidence(ValueError):
    pass


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidEvidence("duplicate JSON key")
        result[key] = value
    return result


def _strict_json(raw: bytes):
    if len(raw) > MAX_RESPONSE_BYTES:
        raise InvalidEvidence("oversized runner-live evidence")
    try:
        data = json.loads(raw.decode("utf-8", errors="strict"),
                          object_pairs_hook=_unique_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(
                              InvalidEvidence("non-finite JSON value")))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise InvalidEvidence("malformed runner-live JSON") from exc
    if not isinstance(data, dict):
        raise InvalidEvidence("runner-live top-level must be an object")
    return data


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise InvalidEvidence("runner-live redirected")


def read_live(fixture: Path | None = None) -> dict:
    if fixture is not None:
        with fixture.open("rb") as stream:
            raw = stream.read(MAX_RESPONSE_BYTES + 1)
        return _strict_json(raw)
    request = urllib.request.Request(API, headers={"Accept": "application/json"},
                                     method="GET")
    opener = urllib.request.build_opener(_NoRedirect)
    with opener.open(request, timeout=5) as response:
        if response.status != 200 or response.headers.get_content_type() != "application/json":
            raise InvalidEvidence("runner-live must return HTTP 200 application/json")
        return _strict_json(response.read(MAX_RESPONSE_BYTES + 1))


def _positive_int(value, label):
    if type(value) is not int or not (1 <= value <= MAX_SLOTS):
        raise InvalidEvidence(f"invalid {label}")
    return value


def _project(value):
    if not isinstance(value, str) or not value or len(value) > 80 or not all(
        c.isascii() and (c.isalnum() or c in "_-") for c in value
    ):
        raise InvalidEvidence("invalid project identity")
    return value


def _table_columns(conn, table):
    rows = conn.execute("PRAGMA table_info(" + table + ")").fetchall()
    if not rows:
        raise InvalidEvidence("missing " + table + " table")
    return {row["name"] for row in rows}


def read_roster(db: Path) -> dict:
    """Read both tables under ONE read transaction; no schema init or cleanup."""
    if not db.is_file() or db.is_symlink():
        raise InvalidEvidence("database missing or symlinked")
    path = urllib.parse.quote(str(db.resolve()), safe="/")
    conn = sqlite3.connect("file:" + path + "?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        if not {"slot", "project_id", "worker_slot"}.issubset(
            _table_columns(conn, "ai_global_slots")
        ):
            raise InvalidEvidence("unsupported ai_global_slots schema")
        if not {"project_id", "worker_slot", "desired_state"}.issubset(
            _table_columns(conn, "runner_workers")
        ):
            raise InvalidEvidence("unsupported runner_workers schema")
        slots = [dict(r) for r in conn.execute(
            "SELECT slot,project_id,worker_slot FROM ai_global_slots ORDER BY slot"
        )]
        targets = [dict(r) for r in conn.execute(
            "SELECT project_id,worker_slot,desired_state FROM runner_workers "
            "ORDER BY project_id,worker_slot"
        )]
        if not {"id", "project_id", "action", "status", "created_at"}.issubset(
            _table_columns(conn, "runner_commands")
        ):
            raise InvalidEvidence("unsupported runner_commands schema")
        pending = [dict(r) for r in conn.execute(
            "SELECT project_id,action,created_at FROM runner_commands "
            "WHERE status='pending' AND project_id LIKE '%::w%' "
            "AND action IN ('start','push','force_start','new_chat') "
            "ORDER BY id DESC LIMIT 1025"
        )]
        conn.execute("ROLLBACK")
    finally:
        conn.close()
    if len(slots) > MAX_SLOTS or len(targets) > 4096 or len(pending) > 1024:
        raise InvalidEvidence("roster exceeds diagnostic bounds")
    seen_slots, seen_keys = set(), set()
    for row in slots:
        slot = _positive_int(row["slot"], "global slot")
        key = (_project(row["project_id"]), _positive_int(row["worker_slot"], "worker slot"))
        if slot in seen_slots or key in seen_keys:
            raise InvalidEvidence("duplicate global slot or allocation")
        seen_slots.add(slot)
        seen_keys.add(key)
    target_map = {}
    for row in targets:
        key = (_project(row["project_id"]), _positive_int(row["worker_slot"], "worker slot"))
        if key in target_map:
            raise InvalidEvidence("duplicate worker target")
        if not isinstance(row["desired_state"], str):
            raise InvalidEvidence("invalid desired worker state")
        target_map[key] = row["desired_state"]
    for item in pending:
        key = item["project_id"]
        if not isinstance(key, str) or len(key) > 180 or "::w" not in key:
            raise InvalidEvidence("invalid pending command worker key")
        prefix, sep, suffix = key.rpartition("::w")
        _project(prefix)
        _positive_int(int(suffix) if suffix.isdigit() else None, "pending worker slot")
        if not isinstance(item["created_at"], str):
            raise InvalidEvidence("invalid pending command timestamp")
    return {"slots": slots, "targets": target_map, "pending": pending}


def _live_workers(payload):
    runners = payload.get("chatgpt_runners")
    if not isinstance(runners, dict):
        raise InvalidEvidence("missing chatgpt_runners")
    workers = {}
    for project_id, runner in runners.items():
        _project(project_id)
        if not isinstance(runner, dict) or not isinstance(runner.get("workers"), list):
            raise InvalidEvidence("malformed runner worker collection")
        for item in runner["workers"]:
            if not isinstance(item, dict):
                raise InvalidEvidence("malformed runner worker")
            name = item.get("worker_id")
            if not isinstance(name, str) or len(name) > 180:
                raise InvalidEvidence("invalid live worker identity")
            slot = item.get("worker_slot")
            if slot is not None:
                index = _positive_int(slot, "live worker slot")
            elif name == project_id or name == project_id + "::w1":
                index = 1
            elif name.startswith(project_id + "::w") and name[len(project_id) + 3:].isdigit():
                index = _positive_int(int(name[len(project_id) + 3:]), "live worker slot")
            else:
                raise InvalidEvidence("unmatchable worker identity")
            key = (project_id, index)
            if key in workers:
                raise InvalidEvidence("duplicate live worker key")
            for field in ("active", "generating", "sending"):
                if field in item and type(item[field]) is not bool:
                    raise InvalidEvidence("non-boolean live worker flag")
            workers[key] = item
    return workers


def reconcile(roster: dict, payload: dict, *, now: datetime | None = None) -> dict:
    workers = _live_workers(payload)
    targets = roster["targets"]
    problems = []
    observed = Counter()
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise InvalidEvidence("naive reference clock")
    pending_total = 0
    stale_pending = 0
    # Recent command intent is not proof of dispatch. Detect stuck pending work
    # without ever returning command results, payloads or actor identifiers.
    for cmd in roster.get("pending", []):
        pending_total += 1
        label = cmd["project_id"]
        try:
            when = datetime.fromisoformat(cmd["created_at"].replace("Z", "+00:00"))
            if when.tzinfo is None:
                raise ValueError("naive command timestamp")
            delta = (now - when.astimezone(timezone.utc)).total_seconds()
            if delta < -30:
                raise ValueError("future command timestamp")
        except (TypeError, ValueError, OverflowError):
            problems.append({"worker": label, "code": "pending_age_unverifiable",
                             "level": "degraded"})
            continue
        if delta >= 120:
            stale_pending += 1
            problems.append({"worker": label, "code": "pending_dispatch_over_two_minutes",
                             "level": "attention"})
    for row in roster["slots"]:
        key = (row["project_id"], row["worker_slot"])
        label = f"{key[0]}::w{key[1]}"
        if key not in targets:
            problems.append({"worker": label, "code": "allocation_without_target",
                             "level": "degraded"})
            continue
        state = targets[key].strip().lower()
        if state in ("paused", "draining", "stopped", "disabled"):
            problems.append({"worker": label, "code": "allocated_but_not_runnable",
                             "level": "degraded"})
            continue
        worker = workers.get(key)
        if worker is None or worker.get("active") is False:
            problems.append({"worker": label, "code": "active_worker_not_observed",
                             "level": "degraded"})
            continue
        if worker.get("active") is not True or worker.get("generating") is None or worker.get("sending") is None:
            problems.append({"worker": label, "code": "worker_activity_unknown",
                             "level": "degraded"})
            continue
        if worker["generating"] or worker["sending"]:
            observed["busy"] += 1
            continue
        # "active" is configured allocation, NOT evidence of a connected tab.
        # The server can report active=True together with state="offline".
        live_state = worker.get("state")
        if live_state in ("offline", "stale", "stalled", "disconnected"):
            problems.append({"worker": label, "code": "worker_not_live",
                             "level": "degraded"})
            continue
        if live_state in ("starting", "draining", "paused"):
            problems.append({"worker": label, "code": "worker_not_ready",
                             "level": "attention"})
            continue
        age = worker.get("age_seconds")
        if type(age) not in (int, float) or not (0 <= age <= 86400):
            problems.append({"worker": label, "code": "worker_activity_unknown",
                             "level": "degraded"})
        elif age > 300:
            problems.append({"worker": label, "code": "idle_over_five_minutes",
                             "level": "attention"})
        else:
            observed["idle"] += 1
    problems.sort(key=lambda x: (x["worker"], x["code"]))
    degraded = any(p["level"] == "degraded" for p in problems)
    return {
        "schema_version": 1,
        "diagnostic_only": True,
        "authorizes_mutation": False,
        "state": "degraded" if degraded else ("attention" if problems else "ok"),
        "allocated_count": len(roster["slots"]),
        "pending_dispatch_count": pending_total,
        "stale_pending_dispatch_count": stale_pending,
        "busy_count": observed["busy"],
        "recent_idle_count": observed["idle"],
        "problem_counts": dict(sorted(Counter(p["code"] for p in problems).items())),
        "problems": problems,
        "limitations": [
            "No Firefox tab inventory: unobserved workers do not prove missing tabs.",
            "Runner-live activity does not prove actual prompt submission or product progress.",
            "Observe only; never use this report to authorize force-start, deploy or restart.",
        ],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True,
                        help="existing local history.db (opened read-only)")
    parser.add_argument("--runner-live-fixture", type=Path,
                        help="offline exact JSON fixture; never contacts API")
    args = parser.parse_args(argv)
    try:
        report = reconcile(read_roster(args.db), read_live(args.runner_live_fixture))
    except (InvalidEvidence, OSError, sqlite3.Error, ValueError) as exc:
        report = {"schema_version": 1, "diagnostic_only": True,
                  "authorizes_mutation": False, "state": "unknown",
                  "error_code": type(exc).__name__}
    print(json.dumps(report, sort_keys=True, ensure_ascii=True))
    return 0 if report["state"] == "ok" else 2


if __name__ == "__main__":
    sys.exit(main())

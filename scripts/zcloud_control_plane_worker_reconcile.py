#!/usr/bin/env python3
"""Read-only reconciliation of zCloud scheduler slots, worker targets and live evidence.

Operator diagnostic, NOT an admission or recovery tool. It never starts a tab,
dispatches a prompt, restarts a service, writes SQLite or authorizes a deploy.
The report intentionally excludes conversation IDs, prompts and command bodies.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

API = "http://127.0.0.1:8765/api/runner-live"
MAX_RESPONSE_BYTES = 256 * 1024
MAX_JSON_DEPTH = 64
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


def _finite_float(token: str) -> float:
    value = float(token)
    if not math.isfinite(value):
        raise InvalidEvidence("non-finite JSON number")
    return value


def _strict_json(raw: bytes):
    if len(raw) > MAX_RESPONSE_BYTES:
        raise InvalidEvidence("oversized runner-live evidence")
    try:
        decoded = raw.decode("utf-8", errors="strict")
        # Reject pathological nesting BEFORE json.loads. Python's effective
        # recursion limit differs between runners, so relying on its parser
        # exception is neither deterministic nor safe. Brackets within JSON
        # string literals (including escaped quotes) never count as nesting.
        depth = 0
        in_string = False
        escaped = False
        for char in decoded:
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
            elif char == '"':
                in_string = True
            elif char in "[{":
                depth += 1
                if depth > MAX_JSON_DEPTH:
                    raise InvalidEvidence("runner-live JSON nesting exceeds limit")
            elif char in "]}":
                depth -= 1
                if depth < 0:
                    raise InvalidEvidence("invalid runner-live JSON delimiters")
        if depth != 0 or in_string:
            raise InvalidEvidence("incomplete runner-live JSON")
        data = json.loads(decoded, object_pairs_hook=_unique_pairs,
                          parse_float=_finite_float,
                          parse_constant=lambda _: (_ for _ in ()).throw(
                              InvalidEvidence("non-finite JSON value")))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise InvalidEvidence("malformed runner-live JSON") from exc
    if not isinstance(data, dict):
        raise InvalidEvidence("runner-live top-level must be an object")
    # Escaped unpaired surrogate sequences are accepted by Python json.loads
    # but are invalid Unicode scalar values and unsafe for other consumers.
    try:
        json.dumps(data, ensure_ascii=False).encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise InvalidEvidence("runner-live contains invalid Unicode surrogate") from exc
    return data


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise InvalidEvidence("runner-live redirected")


def _fresh_server_time(payload: dict) -> None:
    # The real runner-live route emits now() in UTC. A cached/replayed response
    # may claim state=live and age_seconds=10 indefinitely if this provenance is
    # not checked against the same host's clock. Offline fixture mode remains
    # intentionally exempt because it never establishes live provenance.
    stamp = payload.get("time")
    if not isinstance(stamp, str) or not stamp or len(stamp) > 64:
        raise InvalidEvidence("runner-live server time missing or invalid")
    try:
        when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if when.tzinfo is None:
            raise ValueError("naive server time")
        delta = (datetime.now(timezone.utc) - when.astimezone(timezone.utc)).total_seconds()
        if not (-30 <= delta <= 30):
            raise ValueError("stale or future server snapshot")
    except (ValueError, TypeError, OverflowError) as exc:
        raise InvalidEvidence("runner-live server time unverified") from exc


def read_live(fixture: Path | None = None) -> dict:
    if fixture is not None:
        with fixture.open("rb") as stream:
            raw = stream.read(MAX_RESPONSE_BYTES + 1)
        return _strict_json(raw)
    request = urllib.request.Request(API, headers={"Accept": "application/json"},
                                     method="GET")
    # A globally configured HTTP_PROXY can otherwise intercept requests to
    # 127.0.0.1, leaking the endpoint path and returning forged runner status.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    with opener.open(request, timeout=5) as response:
        if response.status != 200 or response.headers.get_content_type() != "application/json":
            raise InvalidEvidence("runner-live must return HTTP 200 application/json")
        payload = _strict_json(response.read(MAX_RESPONSE_BYTES + 1))
    _fresh_server_time(payload)
    return payload


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
        if not {"slot", "project_id", "worker_slot", "assigned_at"}.issubset(
            _table_columns(conn, "ai_global_slots")
        ):
            raise InvalidEvidence("unsupported ai_global_slots schema")
        if not {"project_id", "worker_slot", "desired_state"}.issubset(
            _table_columns(conn, "runner_workers")
        ):
            raise InvalidEvidence("unsupported runner_workers schema")
        slots = [dict(r) for r in conn.execute(
            "SELECT slot,project_id,worker_slot,assigned_at FROM ai_global_slots ORDER BY slot"
        )]
        targets = [dict(r) for r in conn.execute(
            "SELECT project_id,worker_slot,desired_state FROM runner_workers "
            "ORDER BY project_id,worker_slot"
        )]
        if not {"queue_id", "project_id", "worker_slot", "status", "claim_expires", "updated_at"}.issubset(
            _table_columns(conn, "portfolio_queue")
        ):
            raise InvalidEvidence("unsupported portfolio_queue schema")
        # Never read titles, prompts, claim metadata, or work item evidence.
        # Read all relevant queue owners within the same transaction to detect
        # conflicting simultaneous claims of a single global worker slot.
        queue_rows = [dict(r) for r in conn.execute(
            "SELECT queue_id,project_id,worker_slot,claim_expires,updated_at FROM portfolio_queue "
            "WHERE worker_slot IS NOT NULL AND status IN ('claimed','running','verifying') "
            "ORDER BY updated_at DESC LIMIT 1025"
        )]
        if not {"project_id", "active", "worker_count"}.issubset(
            _table_columns(conn, "runner_targets")
        ):
            raise InvalidEvidence("unsupported runner_targets schema")
        project_rows = [dict(r) for r in conn.execute(
            "SELECT project_id,active,worker_count FROM runner_targets ORDER BY project_id"
        )]
        if not {"id", "project_id", "action", "status", "created_at"}.issubset(
            _table_columns(conn, "runner_commands")
        ):
            raise InvalidEvidence("unsupported runner_commands schema")
        pending = [dict(r) for r in conn.execute(
            "SELECT id,project_id,action,created_at FROM runner_commands "
            "WHERE status='pending' AND project_id LIKE '%::w%' "
            "AND action IN ('start','push','force_start','new_chat') "
            "ORDER BY id DESC LIMIT 1025"
        )]
        conn.execute("ROLLBACK")
    finally:
        conn.close()
    if (len(slots) > MAX_SLOTS or len(targets) > 4096 or len(project_rows) > 4096
            or len(pending) > 1024 or len(queue_rows) > 1024):
        raise InvalidEvidence("roster exceeds diagnostic bounds")
    seen_slots, seen_keys = set(), set()
    for row in slots:
        slot = _positive_int(row["slot"], "global slot")
        key = (_project(row["project_id"]), _positive_int(row["worker_slot"], "worker slot"))
        if slot in seen_slots or key in seen_keys:
            raise InvalidEvidence("duplicate global slot or allocation")
        if not isinstance(row["assigned_at"], str) or len(row["assigned_at"]) > 64:
            raise InvalidEvidence("invalid global slot assignment watermark")
        seen_slots.add(slot)
        seen_keys.add(key)
    target_map = {}
    for row in targets:
        key = (_project(row["project_id"]), _positive_int(row["worker_slot"], "worker slot"))
        if key in target_map:
            raise InvalidEvidence("duplicate worker target")
        state = row["desired_state"]
        if not isinstance(state, str) or state.strip().lower() not in (
            "running", "paused", "draining", "stopped", "disabled"
        ):
            raise InvalidEvidence("invalid desired worker state")
        target_map[key] = state
    project_targets = {}
    for row in project_rows:
        project = _project(row["project_id"])
        if project in project_targets:
            raise InvalidEvidence("duplicate project target")
        if type(row["active"]) is not int or row["active"] not in (0, 1):
            raise InvalidEvidence("invalid project active flag")
        # A target count smaller than the worker's slot would hide that slot
        # in the runtime projector even if ai_global_slots still allocates it.
        count = _positive_int(row["worker_count"], "target worker count")
        project_targets[project] = {"active": bool(row["active"]), "worker_count": count}
    for row in queue_rows:
        if not isinstance(row["queue_id"], str) or not row["queue_id"] or len(row["queue_id"]) > 160:
            raise InvalidEvidence("invalid queue claim identity")
        if not isinstance(row["updated_at"], str) or len(row["updated_at"]) > 64:
            raise InvalidEvidence("invalid queue claim watermark")
        _project(row["project_id"])
        _positive_int(row["worker_slot"], "queue global slot")
        until = row["claim_expires"]
        if until is not None and (not isinstance(until, str) or len(until) > 64):
            raise InvalidEvidence("invalid queue claim deadline")
    for item in pending:
        if type(item["id"]) is not int or item["id"] <= 0:
            raise InvalidEvidence("invalid pending command sequence")
        key = item["project_id"]
        if not isinstance(key, str) or len(key) > 180 or "::w" not in key:
            raise InvalidEvidence("invalid pending command worker key")
        prefix, sep, suffix = key.rpartition("::w")
        _project(prefix)
        _positive_int(int(suffix) if suffix.isdigit() else None, "pending worker slot")
        if not isinstance(item["created_at"], str):
            raise InvalidEvidence("invalid pending command timestamp")
    return {"slots": slots, "targets": target_map, "project_targets": project_targets,
            "queue_rows": queue_rows, "pending": pending}


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
            # Never allow a claimed worker_slot to relabel a *different* worker.
            # Identity mismatch previously made cloud::w2 masquerade as cloud::w1.
            if name.startswith(project_id + "::w"):
                digits = name[len(project_id) + 3:]
                if not digits.isascii() or not digits.isdecimal():
                    raise InvalidEvidence("invalid worker identity suffix")
                canonical_slot = _positive_int(int(digits), "live identity slot")
                if digits != str(canonical_slot):
                    raise InvalidEvidence("noncanonical live worker identity")
            else:
                raise InvalidEvidence("unmatchable worker identity")
            slot = item.get("worker_slot")
            index = (_positive_int(slot, "live worker slot")
                     if slot is not None else canonical_slot)
            if index != canonical_slot:
                raise InvalidEvidence("worker identity and slot disagree")
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
    # Identity-safe queue correlation; a persisted global allocation might be
    # from manual/bootstrap state, so absence is attention not authorization.
    # Time-bounded claimed rows are evaluated against one UTC reference.
    queue_by_slot = {}
    for claim in roster.get("queue_rows", []):
        deadline = claim["claim_expires"]
        if deadline:
            try:
                parsed = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    raise ValueError("naive claim deadline")
                if parsed.astimezone(timezone.utc) <= now.astimezone(timezone.utc):
                    continue
            except (TypeError, ValueError, OverflowError):
                problems.append({"worker": f"{claim['project_id']}::slot{claim['worker_slot']}",
                                 "code": "queue_claim_deadline_unverifiable", "level": "degraded"})
                continue
        queue_by_slot.setdefault(claim["worker_slot"], []).append(claim["project_id"])
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
    # A live queue claim without any assigned global slot can explain why a
    # task stays claimed while no tab receives a new prompt. This can also be
    # a transient scheduler reallocation; classify as attention, never restart.
    assigned_slots = {row["slot"] for row in roster["slots"]}
    for global_slot, projects in sorted(queue_by_slot.items()):
        if global_slot not in assigned_slots:
            problems.append({"worker": f"global::slot{global_slot}",
                             "code": "live_queue_claim_without_global_slot",
                             "level": "attention"})
            if len(projects) > 1:
                problems.append({"worker": f"global::slot{global_slot}",
                                 "code": "multiple_live_queue_claims",
                                 "level": "degraded"})
    assigned_project_counts = Counter(row["project_id"] for row in roster["slots"])
    for row in roster["slots"]:
        key = (row["project_id"], row["worker_slot"])
        label = f"{key[0]}::w{key[1]}"
        if "queue_rows" in roster:
            claimed_projects = queue_by_slot.get(row["slot"], [])
            if len(claimed_projects) > 1:
                problems.append({"worker": label, "code": "multiple_live_queue_claims",
                                 "level": "degraded"})
            elif len(claimed_projects) == 1 and claimed_projects[0] != key[0]:
                problems.append({"worker": label, "code": "queue_claim_allocation_mismatch",
                                 "level": "degraded"})
            elif not claimed_projects:
                # During bootstrap no queue-owned work is required. Attention
                # only; never assert browser absence or permission to retry.
                problems.append({"worker": label, "code": "allocation_without_live_queue_claim",
                                 "level": "attention"})
        project_target = roster.get("project_targets", {}).get(key[0])
        if project_target is None and "project_targets" in roster:
            problems.append({"worker": label, "code": "allocation_without_project_target",
                             "level": "degraded"})
            continue
        if project_target is not None:
            # server.py expands target count to max(configured count, number
            # of queue-allocated project workers), but iterates ordinals 1..N.
            # A high ordinal can therefore disappear only if above that
            # effective count, not merely above the configured dashboard value.
            projected_count = max(project_target["worker_count"],
                                  assigned_project_counts[key[0]])
            if key[1] > projected_count:
                problems.append({"worker": label, "code": "allocation_outside_projected_worker_count",
                                 "level": "degraded"})
                continue
            if key[1] > project_target["worker_count"]:
                problems.append({"worker": label, "code": "worker_count_expanded_by_allocation",
                                 "level": "attention"})
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
        # The live API's active field is configured allocation, NOT a verified
        # Firefox tab. Reconcile actual state BEFORE treating generating as busy.
        if worker.get("active") is not True or worker.get("generating") is None or worker.get("sending") is None:
            problems.append({"worker": label, "code": "worker_activity_unknown",
                             "level": "degraded"})
            continue
        live_state = worker.get("state")
        if live_state == "stalled":
            problems.append({"worker": label, "code": "generation_stalled",
                             "level": "degraded"})
            continue
        if live_state in ("offline", "stale", "disconnected"):
            problems.append({"worker": label, "code": "worker_not_live",
                             "level": "degraded"})
            continue
        if live_state in ("starting", "draining", "paused"):
            problems.append({"worker": label, "code": "worker_not_ready",
                             "level": "attention"})
            continue
        if live_state != "live":
            problems.append({"worker": label, "code": "worker_state_unknown",
                             "level": "degraded"})
            continue
        age = worker.get("age_seconds")
        if type(age) not in (int, float) or not (0 <= age <= 90):
            problems.append({"worker": label, "code": "worker_activity_unknown",
                             "level": "degraded"})
            continue
        if worker["generating"] or worker["sending"]:
            observed["busy"] += 1
        else:
            observed["idle"] += 1
    # Conversely, the browser/runner projector may show an active worker that
    # is absent from the persisted global slot map (e.g. manual bootstrap).
    # This is useful routing evidence, not proof an unexpected tab is open.
    allocated_keys = {(row["project_id"], row["worker_slot"]) for row in roster["slots"]}
    for key, observed_worker in sorted(workers.items()):
        if key not in allocated_keys and observed_worker.get("active") is True:
            problems.append({"worker": f"{key[0]}::w{key[1]}",
                             "code": "active_observation_without_persisted_slot",
                             "level": "attention"})
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


def observe(db: Path, fixture: Path | None = None) -> dict:
    """Bracket the HTTP sample with coherent SQLite observations.

    Concurrent allocation/command writes can otherwise make a worker look
    "missing" even though it was legitimately reassigned between the reads.
    In that case the result is unknown, never a spurious restart instruction.
    """
    before = read_roster(db)
    live = read_live(fixture)
    after = read_roster(db)
    if before != after:
        raise InvalidEvidence("scheduler changed during live worker observation")
    return reconcile(after, live)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True,
                        help="existing local history.db (opened read-only)")
    parser.add_argument("--runner-live-fixture", type=Path,
                        help="offline exact JSON fixture; never contacts API")
    args = parser.parse_args(argv)
    try:
        report = observe(args.db, args.runner_live_fixture)
    except (InvalidEvidence, OSError, sqlite3.Error, ValueError) as exc:
        report = {"schema_version": 1, "diagnostic_only": True,
                  "authorizes_mutation": False, "state": "unknown",
                  "error_code": type(exc).__name__}
    # Offline fixture mode can be internally consistent while proving nothing
    # about the currently running Firefox/browser. Machine-readable provenance
    # must survive success as well as every fail-closed error path.
    report["evidence_mode"] = (
        "offline_fixture" if args.runner_live_fixture is not None else "loopback_http"
    )
    report["browser_generation_verified"] = False
    print(json.dumps(report, sort_keys=True, ensure_ascii=True))
    return 0 if report["state"] == "ok" else 2


if __name__ == "__main__":
    sys.exit(main())

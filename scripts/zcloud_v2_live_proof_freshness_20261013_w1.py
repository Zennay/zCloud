#!/usr/bin/env python3
"""Read-only, fail-closed Worker-v2 heartbeat proof, never deploy authority.

All diagnostic output is bounded aggregate counts / deny reason names, not
worker IDs, HTTP bodies, process arguments, environment, or SQLite rows.
"""
from collections import Counter
from datetime import datetime, timezone
import json
import re
import sqlite3
import sys
import urllib.request
from pathlib import Path

API = "http://127.0.0.1:8765/api/runner-live"
DB = Path("/home/ubuntu/zennay-cloud/history.db")
MAX_BYTES = 65536
MAX_ACTIVE = 128
MAX_AGE_SECONDS = 90
FUTURE_SKEW_SECONDS = 10
REASON = "worker-v2-check-30s"
WORKER_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}::w[1-9][0-9]*$")


class ProofDenied(ValueError):
    pass


def unique_json(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ProofDenied("duplicate JSON properties")
        result[name] = value
    return result


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise ProofDenied("redirect to nonlocal source")


def parse_time(value, now):
    if not isinstance(value, str) or len(value) > 48:
        raise ProofDenied("missing or malformed heartbeat time")
    try:
        candidate = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProofDenied("invalid heartbeat time") from exc
    if candidate.tzinfo is None:
        raise ProofDenied("timezone missing")
    age = (now - candidate.astimezone(timezone.utc)).total_seconds()
    if age < -FUTURE_SKEW_SECONDS or age > MAX_AGE_SECONDS:
        raise ProofDenied("stale or future heartbeat")
    return age


def active_workers(data):
    if not isinstance(data, dict):
        raise ProofDenied("status response is not a map")
    runners = data.get("chatgpt_runners")
    if not isinstance(runners, dict) or not runners:
        raise ProofDenied("no runner evidence")
    selected = set()
    for runner_name, runner in runners.items():
        if not isinstance(runner_name, str) or not runner_name or not isinstance(runner, dict):
            raise ProofDenied("ambiguous runner identity")
        workers = runner.get("workers")
        if not isinstance(workers, list):
            raise ProofDenied("runner worker inventory absent")
        for worker in workers:
            if not isinstance(worker, dict) or type(worker.get("active")) is not bool:
                raise ProofDenied("untyped active status")
            if not worker["active"]:
                continue
            wid = worker.get("worker_id")
            slot = worker.get("worker_slot")
            if not isinstance(wid, str) or not WORKER_ID.fullmatch(wid):
                raise ProofDenied("invalid active worker identity")
            if type(slot) is not int or not (1 <= slot <= 128):
                raise ProofDenied("missing or invalid worker slot")
            prefix, encoded_slot = wid.rsplit("::w", 1)
            if int(encoded_slot) != slot or not prefix:
                raise ProofDenied("worker identity and slot mismatch")
            item = (prefix, slot)
            if item in selected:
                raise ProofDenied("duplicate worker across runners")
            selected.add(item)
            if len(selected) > MAX_ACTIVE:
                raise ProofDenied("active worker inventory too large")
    if not selected:
        raise ProofDenied("empty active worker inventory")
    return tuple(sorted(selected))


def verify_receipts(workers, rows, now):
    """rows: mapping of (project,slot) -> (timestamp,reason, generating,sending).

    Live DB caller always supplies the latest heartbeat event by monotonic id,
    not the latest row matching a desired reason. Missing rows deny.
    """
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ProofDenied("missing aware observation clock")
    if set(rows) != set(workers):
        raise ProofDenied("heartbeat receipt coverage mismatch")
    counts = Counter()
    for item in workers:
        value = rows[item]
        if not isinstance(value, (tuple, list)) or len(value) != 4:
            raise ProofDenied("missing structured heartbeat")
        ts, reason, generating, sending = value
        parse_time(ts, now)
        if reason != REASON:
            raise ProofDenied("latest heartbeat not from v2 engine")
        if generating not in (0, 1) or type(generating) is not int:
            raise ProofDenied("invalid heartbeat generating flag")
        if sending not in (0, 1) or type(sending) is not int:
            raise ProofDenied("invalid heartbeat sending flag")
        counts["v2_recent"] += 1
    return {"active_workers": len(workers), "recent_v2_heartbeats": counts["v2_recent"]}


def live_status(opener=None):
    client = opener or urllib.request.build_opener(NoRedirect)
    request = urllib.request.Request(
        API, headers={"Accept": "application/json", "User-Agent": "zcloud-v2-live-proof"}
    )
    with client.open(request, timeout=5) as response:
        if response.status != 200 or response.headers.get_content_type() != "application/json":
            raise ProofDenied("status HTTP evidence not trusted")
        content = response.read(MAX_BYTES + 1)
        if len(content) > MAX_BYTES:
            raise ProofDenied("status body too large")
        payload = json.loads(content.decode("utf-8"), object_pairs_hook=unique_json)
    return active_workers(payload)


def latest_sqlite_receipts(workers, db=DB):
    if not db.is_file() or db.is_symlink():
        raise ProofDenied("live history database unavailable")
    # SQLite mode=ro plus query_only ensures no writes, schema migrations or
    # WAL checkpoint. All SQL predicates are parameter-bound.
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=3)
    try:
        con.execute("PRAGMA query_only=ON")
        rows = {}
        for project, slot in workers:
            row = con.execute(
                "SELECT ts, reason, generating, sending FROM runner_events "
                "WHERE project_id=? AND worker_slot=? AND event='heartbeat' "
                "ORDER BY id DESC LIMIT 1", (project, slot)
            ).fetchone()
            if row is None:
                raise ProofDenied("missing latest worker heartbeat")
            rows[(project, slot)] = row
        return rows
    finally:
        con.close()


def main():
    try:
        workers = live_status()
        rows = latest_sqlite_receipts(workers)
        result = verify_receipts(workers, rows, datetime.now(timezone.utc))
        result.update({
            "status": "pass",
            "mutation_performed": False,
            "production_admission_authorized": False,
        })
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ProofDenied, OSError, ValueError, UnicodeError, sqlite3.DatabaseError, TypeError) as exc:
        print(json.dumps({
            "status": "deny",
            "reason_class": type(exc).__name__,
            "mutation_performed": False,
            "production_admission_authorized": False,
        }, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())

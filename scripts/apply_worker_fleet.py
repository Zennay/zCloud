#!/usr/bin/env python3
"""Apply a host-bound worker fleet to the existing zCloud SQLite state.

This changes desired state, not credentials, logins, browser tabs or GitHub
runner registration. Run with --apply only after the new VPS migration is ready.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import socket
import sqlite3
import sys

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "worker-fleet.json"
DB = ROOT / "history.db"
REQUIRED_TABLES = {"runner_targets", "runner_workers", "runtime_settings", "ai_global_slots", "portfolio_queue", "task_claims"}


def get_plan() -> tuple[dict, list[tuple[str, int, int]]]:
    plan = json.loads(CONFIG.read_text(encoding="utf-8"))
    projects = plan["projects"]
    if not isinstance(projects, dict) or not projects:
        raise ValueError("A nonempty projects map is required")
    if any(not isinstance(n, int) or isinstance(n, bool) or n < 1 for n in projects.values()):
        raise ValueError("Each project needs a positive integer worker count")
    count = sum(projects.values())
    if count != plan["active_workers"] or count > plan["maximum_workers"]:
        raise ValueError("Requested worker counts do not match active/maximum capacity")
    providers = plan["providers"]
    if any(type(providers.get(provider)) is not int or providers[provider] < 0
           for provider in ("chatgpt", "claude")):
        raise ValueError("Invalid provider worker count")
    if providers["chatgpt"] + providers["claude"] != count:
        raise ValueError("Provider counts must equal the active worker count")
    if socket.gethostname() != plan["target_hostname"]:
        raise RuntimeError("Refusing to apply the fleet on the wrong host")
    server_src = (ROOT / "server.py").read_text(encoding="utf-8")
    max_match = re.search(r"^MAX_CHATGPT_WORKERS\s*=\s*(\d+)\s*$", server_src, re.MULTILINE)
    provider_match = re.search(r"^MAX_DYNAMIC_WORKERS_PER_PROVIDER\s*=\s*(\d+)\s*$",
                               server_src, re.MULTILINE)
    if not max_match or int(max_match.group(1)) < plan["maximum_workers"]:
        raise RuntimeError("Deploy the updated server.py worker capacity first")
    if not provider_match or int(provider_match.group(1)) < max(providers.values()):
        raise RuntimeError("Deploy the updated per-provider capacity first")
    contracts = json.loads((ROOT / "project-contracts.json").read_text(encoding="utf-8"))
    for project_id, desired in projects.items():
        contract = contracts.get("projects", {}).get(project_id, {})
        hard_cap = int(contract.get("ai_worker_cap", contracts.get("defaults", {}).get("ai_worker_cap", 1)))
        if hard_cap < desired:
            raise RuntimeError(f"{project_id} hard cap {hard_cap} is below requested {desired}")
    slots = []
    global_slot = 1
    for project_id, desired in projects.items():
        for local_slot in range(1, desired + 1):
            slots.append((project_id, local_slot, global_slot))
            global_slot += 1
    return plan, slots


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Persist desired worker state")
    args = parser.parse_args()
    plan, slots = get_plan()
    if not DB.is_file():
        raise RuntimeError(f"No existing zCloud state at {DB}; do not create an empty database")
    conn = sqlite3.connect(f"file:{DB}?mode={'rw' if args.apply else 'ro'}", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing = REQUIRED_TABLES - tables
        if missing:
            raise RuntimeError(f"Uninitialized zCloud SQLite schema: {sorted(missing)}")
        # Admission and the desired-state rewrite share ONE SQLite transaction.
        # Acquiring the write reservation first prevents a local queue writer
        # from claiming work between the idle check and fleet reallocation.
        conn.execute("BEGIN IMMEDIATE" if args.apply else "BEGIN")
        existing = {r["project_id"] for r in conn.execute("SELECT project_id FROM runner_targets")}
        missing_projects = set(plan["projects"]) - existing
        if missing_projects:
            raise RuntimeError(f"Unknown targets; update/initialize zCloud first: {sorted(missing_projects)}")
        now = datetime.now(timezone.utc).isoformat()
        # A running/verifying row with an expired or absent lease is still an
        # unresolved handoff. Do not silently steal it during a VPS cutover.
        unresolved_queue = conn.execute(
            "SELECT count(*) FROM portfolio_queue "
            "WHERE status IN ('running','verifying')",
        ).fetchone()[0]
        # Project-level leases can be active without a worker_slot on a queue
        # row. Unknown/malformed timestamps fail closed, not as expired.
        live_project_claims = conn.execute(
            "SELECT count(*) FROM task_claims "
            "WHERE lease_until IS NULL OR julianday(lease_until) IS NULL "
            "OR julianday(lease_until) >= julianday(?)",
            (now,),
        ).fetchone()[0]
        if args.apply:
            if unresolved_queue or live_project_claims:
                raise RuntimeError(
                    f"{unresolved_queue} unresolved queue rows and "
                    f"{live_project_claims} live/unknown project leases; "
                    "drain or reconcile both before cutover"
                )
            conn.execute("UPDATE runner_targets SET active=0")
            conn.execute("UPDATE runner_workers SET desired_state='paused'")
            providers_by_worker = {
                (pid, local): ("chatgpt" if global_slot <= plan["providers"]["chatgpt"] else "claude")
                for pid, local, global_slot in slots
            }
            for project_id, desired in plan["projects"].items():
                conn.execute("UPDATE runner_targets SET active=1, worker_count=? WHERE project_id=?",
                             (desired, project_id))
                primary = conn.execute("SELECT conversation_id FROM runner_targets WHERE project_id=?",
                                       (project_id,)).fetchone()["conversation_id"]
                for worker_slot in range(1, desired + 1):
                    provider = providers_by_worker[(project_id, worker_slot)]
                    conn.execute(
                        "INSERT OR IGNORE INTO runner_workers("
                        "project_id,worker_slot,conversation_id,desired_state,provider)"
                        " VALUES(?,?,?,?,?)",
                        (project_id, worker_slot, primary if worker_slot == 1 else "", "running", provider),
                    )
                    conn.execute("UPDATE runner_workers SET desired_state='running',provider=? "
                                 "WHERE project_id=? AND worker_slot=?",
                                 (provider, project_id, worker_slot))
            values = {
                "dynamic_worker_limit": plan["active_workers"],
                "dynamic_worker_chatgpt_count": plan["providers"]["chatgpt"],
                "dynamic_worker_claude_count": plan["providers"]["claude"],
                "manual_selection_mode": 1,
            }
            for name, value in values.items():
                conn.execute(
                    "INSERT INTO runtime_settings(key,value,updated_at,actor) VALUES(?,?,?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                    "updated_at=excluded.updated_at,actor=excluded.actor",
                    (name, str(value), now, "new-vps-fleet-20261022"),
                )
            conn.execute("DELETE FROM ai_global_slots")
            for project_id, worker_slot, global_slot in slots:
                conn.execute(
                    "INSERT INTO ai_global_slots(slot,project_id,worker_slot,assigned_at)"
                    " VALUES(?,?,?,?)",
                    (global_slot, project_id, worker_slot, now),
                )
            conn.commit()
        actual = [
            dict(r) for r in conn.execute(
                "SELECT project_id,active,worker_count FROM runner_targets WHERE active=1 ORDER BY project_id"
            )
        ]
        global_slots = [
            dict(r) for r in conn.execute(
                "SELECT slot,project_id,worker_slot FROM ai_global_slots ORDER BY slot"
            )
        ]
        print(json.dumps({
            "hostname": socket.gethostname(),
            "applied": args.apply,
            "requested_active": plan["active_workers"],
            "maximum": plan["maximum_workers"],
            "desired_projects": plan["projects"],
            "active_targets_in_db": actual,
            "slots_in_db": global_slots,
            "verified_running_browser_workers": None,
            "cutover_gate": {
                "unresolved_queue_rows": unresolved_queue,
                "live_or_unknown_project_leases": live_project_claims,
                "clear": unresolved_queue == 0 and live_project_claims == 0,
            },
            "note": "Desired-state SQLite changes do not prove running browsers. "
                    "Start the services, inspect /api/runner-live and confirm recent worker heartbeats.",
        }, indent=2))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(f"Refused: {exc}", file=sys.stderr)
        sys.exit(1)

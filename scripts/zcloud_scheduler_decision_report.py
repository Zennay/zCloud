#!/usr/bin/env python3
"""Read-only explanation snapshot for zCloud AI scheduler allocation state.

The report intentionally explains only facts that can be proven from the current
SQLite/config/memory snapshot. It does not claim to reconstruct a historical
allocator decision and calls out runtime factors that are not replayed here.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

ACTIVE_STATUSES = ("claimed", "running", "verifying")
READY_STATUSES = ("queued",) + ACTIVE_STATUSES
PRIORITY_RANK = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
MANUAL_INTENT_SECONDS = 180
UNMODELED_RUNTIME_FACTORS = [
    "busy_worker_pin",
    "pending_handoff_floor",
    "autonomy_soft_cap",
    "lane_conflict_filter",
    "force_start_victim_selection",
    "critical_memory_slot_shedding",
]


def _open_ro(db: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _has_table(connection: sqlite3.Connection, name: str) -> bool:
    return bool(
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()
    )


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    if not _has_table(connection, table):
        return set()
    return {str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table})")}


def _safe_int(value, default=0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _load_contracts(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("projects"), dict):
        raise ValueError("project contracts must contain a projects object")
    return payload


def _runtime_settings(connection: sqlite3.Connection) -> dict[str, str]:
    if not _has_table(connection, "runtime_settings"):
        return {}
    return {
        str(row["key"]): str(row["value"] or "")
        for row in connection.execute("SELECT key,value FROM runtime_settings")
    }


def _global_worker_limit(settings: dict[str, str], slots: list[dict]) -> tuple[int, str]:
    chat_key = "dynamic_worker_chatgpt_count"
    claude_key = "dynamic_worker_claude_count"
    if chat_key in settings or claude_key in settings:
        return (
            max(0, _safe_int(settings.get(chat_key))) + max(0, _safe_int(settings.get(claude_key))),
            "provider_runtime_settings",
        )
    if "dynamic_worker_limit" in settings:
        return max(0, _safe_int(settings.get("dynamic_worker_limit"))), "legacy_runtime_setting"
    observed = max((_safe_int(row.get("slot")) for row in slots), default=0)
    return observed, "observed_slots_fallback"


def _manual_intent_active(settings: dict[str, str], project_id: str, prefix: str, now: datetime) -> bool:
    raw = settings.get(f"{prefix}:{project_id}", "")
    if not raw:
        return False
    try:
        started = datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return False
    age = (now - started).total_seconds()
    return 0 <= age <= MANUAL_INTENT_SECONDS


def _recovery_hold_until(settings: dict[str, str], now: datetime) -> str | None:
    raw = str(settings.get("worker_oom_recovery_hold_until") or "").strip()
    if not raw:
        return None
    try:
        until = datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None
    return until.isoformat() if until > now else None


def _memory_snapshot(
    meminfo: Path,
    headroom_mb: int,
    per_new_slot_mb: int,
    warn_mb: int,
    critical_mb: int,
    swap_min_total_mb: int,
    swap_min_free_mb: int,
) -> dict:
    values: dict[str, int] = {}
    try:
        for line in meminfo.read_text(encoding="utf-8").splitlines():
            if ":" not in line:
                continue
            key, raw = line.split(":", 1)
            digits = "".join(ch for ch in raw if ch.isdigit())
            if digits:
                values[key.strip()] = int(digits) // 1024
    except OSError as exc:
        return {
            "available_mb": None,
            "new_worker_capacity": 0,
            "pressure": "unknown",
            "swap_healthy": False,
            "error": str(exc)[:200],
        }

    available = max(0, values.get("MemAvailable", values.get("MemFree", 0)))
    swap_total = max(0, values.get("SwapTotal", 0))
    swap_free = max(0, values.get("SwapFree", 0))
    swap_healthy = (
        swap_total >= swap_min_total_mb
        and swap_free >= min(swap_min_free_mb, swap_total)
    )
    effective_headroom = headroom_mb + (0 if swap_healthy else 512)
    capacity = max(0, (available - effective_headroom) // max(1, per_new_slot_mb))
    if available < critical_mb:
        pressure = "critical"
    elif available < warn_mb:
        pressure = "warning"
    elif capacity <= 0:
        pressure = "guarded"
    else:
        pressure = "ok"
    return {
        "available_mb": available,
        "swap_total_mb": swap_total,
        "swap_free_mb": swap_free,
        "swap_healthy": bool(swap_healthy),
        "headroom_mb": headroom_mb,
        "effective_headroom_mb": effective_headroom,
        "per_new_slot_mb": per_new_slot_mb,
        "new_worker_capacity": int(capacity),
        "pressure": pressure,
    }


def _queue_rows(connection: sqlite3.Connection) -> list[dict]:
    required = {"queue_id", "project_id", "priority", "status", "eligible", "worker_slot", "created_at", "updated_at"}
    columns = _columns(connection, "portfolio_queue")
    if not required.issubset(columns):
        return []
    rows = connection.execute(
        """SELECT queue_id,project_id,priority,status,eligible,worker_slot,created_at,updated_at
           FROM portfolio_queue
           WHERE status IN ('queued','claimed','running','verifying')
           ORDER BY created_at,queue_id"""
    ).fetchall()
    return [dict(row) for row in rows]


def _slot_rows(connection: sqlite3.Connection) -> list[dict]:
    required = {"slot", "project_id", "worker_slot", "assigned_at"}
    columns = _columns(connection, "ai_global_slots")
    if not required.issubset(columns):
        return []
    return [
        dict(row)
        for row in connection.execute(
            "SELECT slot,project_id,worker_slot,assigned_at FROM ai_global_slots ORDER BY slot"
        ).fetchall()
    ]


def _target_rows(connection: sqlite3.Connection) -> list[dict]:
    required = {"project_id", "worker_count", "active"}
    columns = _columns(connection, "runner_targets")
    if not required.issubset(columns):
        return []
    return [
        dict(row)
        for row in connection.execute(
            "SELECT project_id,worker_count,active FROM runner_targets ORDER BY project_id"
        ).fetchall()
    ]


def _contract_view(contracts: dict, project_id: str, global_limit: int) -> dict:
    defaults = contracts.get("defaults") or {}
    project = (contracts.get("projects") or {}).get(project_id) or {}
    compute = project.get("compute") or {}
    raw_cap = max(0, _safe_int(project.get("ai_worker_cap", defaults.get("ai_worker_cap", 1)), 1))
    # Mirror server._portfolio_project_hard_cap(): the project cap remains a
    # contract property even while the global browser pool is disabled. Global
    # admission is reported separately rather than collapsing every project cap to 0.
    hard_cap = max(0, min(max(1, global_limit), raw_cap))
    return {
        "queue_mode": str(project.get("queue_mode") or ""),
        "lane_profile": str(project.get("lane_profile") or ""),
        "contract_ai_worker_cap": raw_cap,
        "hard_cap": hard_cap,
        "compute_priority": str(compute.get("priority") or "normal"),
        "compute_pool": str(compute.get("pool") or ""),
        "protected": bool(compute.get("protected")),
    }


def _priority(row: dict) -> int:
    return PRIORITY_RANK.get(str(row.get("priority") or "P3").upper(), 3)


def report(
    db: Path,
    contracts_path: Path,
    meminfo: Path = Path("/proc/meminfo"),
    project: str | None = None,
    now: datetime | None = None,
) -> dict:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    contracts = _load_contracts(contracts_path)
    with closing(_open_ro(db)) as connection:
        settings = _runtime_settings(connection)
        queue = _queue_rows(connection)
        slots = _slot_rows(connection)
        targets = _target_rows(connection)

    global_limit, limit_source = _global_worker_limit(settings, slots)
    memory = _memory_snapshot(
        meminfo,
        max(1536, _safe_int(os.environ.get("ZCLOUD_WORKER_MEMORY_HEADROOM_MB"), 2048)),
        max(512, _safe_int(os.environ.get("ZCLOUD_WORKER_MEMORY_PER_NEW_SLOT_MB"), 1536)),
        max(1024, _safe_int(os.environ.get("ZCLOUD_WORKER_MEMORY_WARN_MB"), 2048)),
        max(512, _safe_int(os.environ.get("ZCLOUD_WORKER_MEMORY_CRITICAL_MB"), 1024)),
        max(0, _safe_int(os.environ.get("ZCLOUD_WORKER_SWAP_MIN_TOTAL_MB"), 1024)),
        max(0, _safe_int(os.environ.get("ZCLOUD_WORKER_SWAP_MIN_FREE_MB"), 512)),
    )
    recovery_hold_until = _recovery_hold_until(settings, now)
    memory["raw_new_worker_capacity"] = memory.get("new_worker_capacity", 0)
    memory["recovery_hold_until"] = recovery_hold_until
    if recovery_hold_until:
        memory["new_worker_capacity"] = 0

    slots_by_project: dict[str, list[dict]] = defaultdict(list)
    for row in slots:
        slots_by_project[str(row.get("project_id") or "")].append(row)

    queue_by_project: dict[str, list[dict]] = defaultdict(list)
    for row in queue:
        queue_by_project[str(row.get("project_id") or "")].append(row)

    targets_by_project = {
        str(row.get("project_id") or ""): row
        for row in targets
        if str(row.get("project_id") or "")
    }

    project_ids = (
        set((contracts.get("projects") or {}).keys())
        | set(slots_by_project)
        | set(queue_by_project)
        | set(targets_by_project)
    )
    if project:
        project_ids = {str(project).strip().lower()}

    eligible_queued = [
        row for row in queue
        if bool(row.get("eligible")) and str(row.get("status")) == "queued"
    ]
    candidate_order = sorted(
        eligible_queued,
        key=lambda row: (
            _priority(row),
            0 if _manual_intent_active(settings, str(row.get("project_id") or ""), "manual_start_priority", now) else 1,
            str(row.get("created_at") or ""),
            str(row.get("queue_id") or ""),
        ),
    )
    top_priority = _priority(candidate_order[0]) if candidate_order else None
    assigned_count = len(slots)
    free_slots = max(0, global_limit - assigned_count)

    projects = []
    for project_id in sorted(project_ids):
        contract = _contract_view(contracts, project_id, global_limit)
        project_slots = sorted(slots_by_project.get(project_id, []), key=lambda row: _safe_int(row.get("slot")))
        project_queue = queue_by_project.get(project_id, [])
        queued = [
            row for row in project_queue
            if bool(row.get("eligible")) and str(row.get("status")) == "queued"
        ]
        active = [
            row for row in project_queue
            if bool(row.get("eligible")) and str(row.get("status")) in ACTIVE_STATUSES
        ]
        own_top = min((_priority(row) for row in queued), default=None)
        manual_start = _manual_intent_active(settings, project_id, "manual_start_priority", now)
        force_start = _manual_intent_active(settings, project_id, "manual_force_start_priority", now)
        target = targets_by_project.get(project_id) or {}
        requested_workers = (
            max(0, _safe_int(target.get("worker_count"), 0))
            if bool(target.get("active"))
            else 0
        )
        allocated_workers = len(project_slots)

        reasons: list[str] = []
        if project_slots:
            reasons.append("assigned_global_slot")
        if force_start:
            reasons.append("manual_force_start_priority_active")
        elif manual_start:
            reasons.append("manual_start_priority_active")
        if contract["queue_mode"] != "execution":
            reasons.append("queue_mode_non_execution")
        if contract["hard_cap"] == 0:
            reasons.append("project_hard_cap_zero")
        if not queued and not active:
            reasons.append("no_eligible_ready_work")
        if queued and len(project_slots) >= contract["hard_cap"] and contract["hard_cap"] >= 0:
            reasons.append("project_hard_cap_reached")
        if (queued or active) and requested_workers > allocated_workers:
            reasons.append("requested_capacity_unmet")
        if queued and recovery_hold_until and free_slots > 0:
            reasons.append("recovery_hold_blocks_new_slot")
        if queued and global_limit <= 0:
            reasons.append("global_worker_pool_disabled")
        elif queued and assigned_count >= global_limit:
            reasons.append("global_worker_pool_full")
        if queued and free_slots > 0 and memory.get("new_worker_capacity", 0) <= 0:
            reasons.append("memory_admission_blocks_new_slot")
        if (
            queued
            and own_top is not None
            and top_priority is not None
            and top_priority < own_top
        ):
            reasons.append("higher_priority_queue_work_present")
        if queued and not project_slots and not reasons:
            reasons.append("waiting_for_unmodeled_runtime_factor")

        projects.append({
            "project_id": project_id,
            "decision_state": "assigned" if project_slots else ("waiting" if queued else "idle"),
            "reason_codes": reasons,
            "assigned_global_slots": [_safe_int(row.get("slot")) for row in project_slots],
            "assigned_worker_slots": [_safe_int(row.get("worker_slot"), 1) for row in project_slots],
            "requested_workers": requested_workers,
            "request_source": "runner_targets.worker_count" if target else None,
            "request_semantics": "configured_browser_capacity_not_allocator_demand" if target else None,
            "allocated_workers": allocated_workers,
            "allocation_delta": allocated_workers - requested_workers,
            "contract_ai_worker_cap": contract["contract_ai_worker_cap"],
            "hard_cap": contract["hard_cap"],
            "queue_mode": contract["queue_mode"],
            "lane_profile": contract["lane_profile"],
            "compute_priority": contract["compute_priority"],
            "compute_pool": contract["compute_pool"],
            "protected": contract["protected"],
            "ready_counts": {
                "queued": len(queued),
                "active": len(active),
            },
            "highest_queued_priority": (
                min((str(row.get("priority") or "P3").upper() for row in queued), key=lambda p: PRIORITY_RANK.get(p, 3))
                if queued else None
            ),
            "queue_ids": [str(row.get("queue_id") or "") for row in queued[:8]],
        })

    return {
        "generated_at": now.isoformat(),
        "scope": "current_snapshot_only",
        "historical_caveat": "This explains current evidence; it does not replay the historical allocator transaction.",
        "unmodeled_runtime_factors": list(UNMODELED_RUNTIME_FACTORS),
        "pool": {
            "configured_limit": global_limit,
            "limit_source": limit_source,
            "assigned_slots": assigned_count,
            "free_slots": free_slots,
            "overallocated_by": max(0, assigned_count - global_limit),
            "state": (
                "disabled" if global_limit <= 0
                else "overallocated" if assigned_count > global_limit
                else "full" if assigned_count == global_limit
                else "available"
            ),
        },
        "memory_admission": memory,
        "candidate_order_semantics": "coarse_priority_and_manual_start_order_not_full_allocator_replay",
        "candidate_order": [
            {
                "queue_id": str(row.get("queue_id") or ""),
                "project_id": str(row.get("project_id") or ""),
                "priority": str(row.get("priority") or "P3").upper(),
                "manual_start_priority_active": _manual_intent_active(
                    settings,
                    str(row.get("project_id") or ""),
                    "manual_start_priority",
                    now,
                ),
            }
            for row in candidate_order[:20]
        ],
        "projects": projects,
    }


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Explain current zCloud scheduler allocation evidence without writing state")
    parser.add_argument("--db", type=Path, default=root / "history.db")
    parser.add_argument("--contracts", type=Path, default=root / "project-contracts.json")
    parser.add_argument("--meminfo", type=Path, default=Path("/proc/meminfo"))
    parser.add_argument("--project")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    payload = report(args.db, args.contracts, args.meminfo, args.project)
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

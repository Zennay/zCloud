#!/usr/bin/env python3
"""Read-only requested-versus-admitted resource allocation report for zCloud.

This report compares each project's canonical compute contract with active
resource leases. "Allocated" here means admitted by the zCloud resource lease
control-plane; it does not claim physical CPU/RAM consumption.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACTS = ROOT / "project-contracts.json"
DEFAULT_DB = ROOT / "history.db"

_REQUIRED_LEASE_COLUMNS = {
    "project_id",
    "pool",
    "workload_class",
    "cpu_soft_cores",
    "memory_soft_mb",
    "lease_until",
}


def _read_json(path: Path) -> dict:
    path = Path(path)
    if path.is_symlink():
        raise ValueError(f"symlink_not_allowed:{path.name}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"unreadable:{path.name}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid_json:{path.name}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"invalid_object:{path.name}")
    return payload


def _contracts(path: Path) -> dict:
    payload = _read_json(path)
    if payload.get("schema_version") != 1:
        raise ValueError("contracts_schema_version")
    projects = payload.get("projects")
    pools = payload.get("resource_pools")
    if not isinstance(projects, dict) or not projects:
        raise ValueError("contracts_projects_missing")
    if not isinstance(pools, dict) or not pools:
        raise ValueError("contracts_pools_missing")
    for pool_name, pool in pools.items():
        if (
            not isinstance(pool, dict)
            or not isinstance(pool.get("slots"), int)
            or isinstance(pool.get("slots"), bool)
            or int(pool["slots"]) < 0
        ):
            raise ValueError(f"invalid_pool:{pool_name}")
    for project_id, contract in projects.items():
        if not isinstance(contract, dict):
            raise ValueError(f"invalid_project:{project_id}")
        compute = contract.get("compute")
        if not isinstance(compute, dict):
            raise ValueError(f"compute_missing:{project_id}")
        pool = str(compute.get("pool") or "")
        if pool not in pools:
            raise ValueError(f"compute_pool_invalid:{project_id}")
        for field in ("cpu_soft_cores", "memory_soft_mb"):
            value = compute.get(field)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
                raise ValueError(f"compute_{field}_invalid:{project_id}")
    return payload


def _parse_time(value: str, *, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid_timestamp:{field}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"naive_timestamp:{field}")
    return parsed.astimezone(timezone.utc)


def _reference_time(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("naive_timestamp:now")
    return now.astimezone(timezone.utc)


def _open_read_only(db_path: Path) -> sqlite3.Connection:
    db_path = Path(db_path)
    if db_path.is_symlink():
        raise ValueError(f"symlink_not_allowed:{db_path.name}")
    if not db_path.exists() or not db_path.is_file():
        raise ValueError(f"unreadable:{db_path.name}")
    try:
        conn = sqlite3.connect(
            f"{db_path.resolve().as_uri()}?mode=ro",
            uri=True,
            timeout=5,
        )
    except sqlite3.Error as exc:
        raise ValueError("db_open_failed") from exc
    conn.row_factory = sqlite3.Row
    return conn


def _active_leases(db_path: Path, reference: datetime) -> list[dict]:
    conn = _open_read_only(db_path)
    try:
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='resource_leases'"
        ).fetchone()
        if not table:
            raise ValueError("resource_leases_missing")
        columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(resource_leases)").fetchall()
        }
        missing = sorted(_REQUIRED_LEASE_COLUMNS - columns)
        if missing:
            raise ValueError("resource_leases_columns_missing:" + ",".join(missing))
        rows = conn.execute(
            """SELECT project_id,pool,workload_class,cpu_soft_cores,
                      memory_soft_mb,lease_until
               FROM resource_leases
               ORDER BY project_id,pool,lease_until"""
        ).fetchall()
    except sqlite3.Error as exc:
        raise ValueError("resource_leases_read_failed") from exc
    finally:
        conn.close()

    active = []
    for row in rows:
        lease_until = _parse_time(row["lease_until"], field="lease_until")
        if lease_until <= reference:
            continue
        active.append(
            {
                "project_id": str(row["project_id"] or "").strip(),
                "pool": str(row["pool"] or "").strip(),
                "workload_class": str(row["workload_class"] or "").strip(),
                "cpu_soft_cores": float(row["cpu_soft_cores"] or 0),
                "memory_soft_mb": int(row["memory_soft_mb"] or 0),
                "lease_until": lease_until.isoformat(),
            }
        )
    return active


def build_report(
    contracts_path: Path = DEFAULT_CONTRACTS,
    db_path: Path = DEFAULT_DB,
    *,
    now: datetime | None = None,
) -> dict:
    contracts = _contracts(Path(contracts_path))
    reference = _reference_time(now)
    leases = _active_leases(Path(db_path), reference)
    projects = contracts["projects"]
    pools = contracts["resource_pools"]

    by_project: dict[str, list[dict]] = defaultdict(list)
    by_pool: dict[str, list[dict]] = defaultdict(list)
    unknown_projects: set[str] = set()
    unknown_pools: set[str] = set()

    for lease in leases:
        project_id = lease["project_id"]
        pool_name = lease["pool"]
        by_project[project_id].append(lease)
        by_pool[pool_name].append(lease)
        if project_id not in projects:
            unknown_projects.add(project_id or "<empty>")
        if pool_name not in pools:
            unknown_pools.add(pool_name or "<empty>")

    project_rows = []
    drift_count = 0
    allocated_project_count = 0
    for project_id in sorted(projects):
        contract = projects[project_id]
        compute = dict(contract.get("compute") or {})
        active = by_project.get(project_id, [])
        reason_codes: list[str] = []
        expected_pool = str(compute.get("pool") or "")
        expected_class = str(compute.get("class") or "")
        expected_cpu = float(compute.get("cpu_soft_cores") or 0)
        expected_memory = int(compute.get("memory_soft_mb") or 0)

        for lease in active:
            if lease["pool"] != expected_pool:
                reason_codes.append("pool_drift")
            if lease["workload_class"] != expected_class:
                reason_codes.append("class_drift")
            if abs(float(lease["cpu_soft_cores"]) - expected_cpu) > 1e-9:
                reason_codes.append("cpu_budget_drift")
            if int(lease["memory_soft_mb"]) != expected_memory:
                reason_codes.append("memory_budget_drift")

        reason_codes = sorted(set(reason_codes))
        if active:
            allocated_project_count += 1
        if reason_codes:
            drift_count += 1

        project_rows.append(
            {
                "project_id": project_id,
                "requested_per_lease": {
                    "pool": expected_pool,
                    "workload_class": expected_class,
                    "cpu_soft_cores": expected_cpu,
                    "memory_soft_mb": expected_memory,
                    "priority": str(compute.get("priority") or ""),
                    "protected": bool(compute.get("protected")),
                },
                "admitted_allocation": {
                    "active_leases": len(active),
                    "cpu_soft_cores_total": round(
                        sum(float(item["cpu_soft_cores"]) for item in active), 3
                    ),
                    "memory_soft_mb_total": sum(
                        int(item["memory_soft_mb"]) for item in active
                    ),
                    "lease_pools": sorted({item["pool"] for item in active}),
                    "workload_classes": sorted(
                        {item["workload_class"] for item in active}
                    ),
                },
                "state": (
                    "allocation_drift"
                    if reason_codes
                    else ("allocated_as_requested" if active else "not_allocated")
                ),
                "reason_codes": reason_codes,
            }
        )

    pool_rows = []
    pool_overcommit_count = 0
    for pool_name in sorted(pools):
        capacity = int(pools[pool_name].get("slots") or 0)
        used = len(by_pool.get(pool_name, []))
        oversubscribed = used > capacity
        if oversubscribed:
            pool_overcommit_count += 1
        pool_rows.append(
            {
                "pool": pool_name,
                "capacity_slots": capacity,
                "admitted_leases": used,
                "available_slots": max(0, capacity - used),
                "oversubscribed": oversubscribed,
            }
        )

    reason_codes = []
    if drift_count:
        reason_codes.append("allocation_contract_drift")
    if pool_overcommit_count:
        reason_codes.append("pool_overcommitted")
    if unknown_projects:
        reason_codes.append("unknown_lease_project")
    if unknown_pools:
        reason_codes.append("unknown_lease_pool")

    return {
        "generated_at": reference.isoformat(),
        "semantics": (
            "requested = canonical per-lease compute contract; admitted allocation = "
            "active zCloud resource leases, not measured physical CPU/RAM consumption"
        ),
        "summary": {
            "project_count": len(project_rows),
            "allocated_project_count": allocated_project_count,
            "unallocated_project_count": len(project_rows) - allocated_project_count,
            "active_lease_count": len(leases),
            "allocation_drift_project_count": drift_count,
            "pool_overcommit_count": pool_overcommit_count,
            "unknown_lease_project_count": len(unknown_projects),
            "unknown_lease_pool_count": len(unknown_pools),
            "contract_consistent": not reason_codes,
            "reason_codes": reason_codes,
        },
        "projects": project_rows,
        "pools": pool_rows,
        "unknown_lease_projects": sorted(unknown_projects),
        "unknown_lease_pools": sorted(unknown_pools),
        "privacy": (
            "owner IDs and lease metadata are intentionally omitted; report contains only "
            "bounded project/pool allocation metadata"
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Report requested versus admitted zCloud resource allocation read-only"
    )
    parser.add_argument("--contracts", type=Path, default=DEFAULT_CONTRACTS)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)
    payload = build_report(args.contracts, args.db)
    print(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2 if args.pretty else None,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Unified zCloud project runtime contracts, evidence receipts and resource admission."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import sqlite3

from lane_generator import SUPPORTED_PROFILES

ROOT = Path(__file__).resolve().parent
CONTRACT_FILE = ROOT / "project-contracts.json"

_ALLOWED_AUTONOMY = {"ai_worker", "zcloud_stopgate", "haxlab_status", "ftmo_status", "external_gate", "manual"}
_MAX_RECEIPT_EVIDENCE_BYTES = 12000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_contracts(path: Path | None = None) -> dict:
    target = path or CONTRACT_FILE
    raw = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ValueError("project-contracts.json schema_version must be 1")
    projects = raw.get("projects")
    pools = raw.get("resource_pools")
    if not isinstance(projects, dict) or not projects:
        raise ValueError("project-contracts.json projects must be a non-empty object")
    if not isinstance(pools, dict) or not pools:
        raise ValueError("project-contracts.json resource_pools must be a non-empty object")
    for name, pool in pools.items():
        if not isinstance(pool, dict) or not isinstance(pool.get("slots"), int) or pool["slots"] < 0:
            raise ValueError(f"resource pool {name!r} requires non-negative integer slots")
    for project_id, contract in projects.items():
        if not isinstance(contract, dict):
            raise ValueError(f"project contract {project_id!r} must be an object")
        lane_profile = str(contract.get("lane_profile") or "").strip().lower()
        if lane_profile not in SUPPORTED_PROFILES:
            raise ValueError(
                f"project {project_id!r} has unsupported lane_profile {lane_profile!r}"
            )
        autonomy = contract.get("autonomy")
        compute = contract.get("compute")
        if not isinstance(autonomy, dict) or autonomy.get("mode") not in _ALLOWED_AUTONOMY:
            raise ValueError(f"project {project_id!r} has invalid autonomy contract")
        if not isinstance(autonomy.get("auto_start"), bool):
            raise ValueError(f"project {project_id!r} autonomy.auto_start must be boolean")
        cap = contract.get("ai_worker_cap")
        if not isinstance(cap, int) or isinstance(cap, bool) or cap < 0:
            raise ValueError(f"project {project_id!r} ai_worker_cap must be >= 0")
        if cap == 0 and (
            autonomy.get("mode") not in {"external_gate", "manual"}
            or autonomy.get("auto_start") is not False
        ):
            raise ValueError(
                f"project {project_id!r} ai_worker_cap=0 requires fail-closed external_gate/manual autonomy"
            )
        if not isinstance(compute, dict) or compute.get("pool") not in pools:
            raise ValueError(f"project {project_id!r} has invalid compute pool")
        if contract.get("queue_mode") == "human-gated":
            if autonomy.get("auto_start") or autonomy.get("mode") not in {"external_gate", "manual"}:
                raise ValueError(f"human-gated project {project_id!r} must fail closed")
            if pools[compute["pool"]]["slots"] != 0:
                raise ValueError(f"human-gated project {project_id!r} must use a disabled resource pool")
    return raw


def project_contract(project_id: str, contracts: dict | None = None) -> dict:
    data = contracts or load_contracts()
    try:
        return data["projects"][str(project_id)]
    except KeyError as exc:
        raise ValueError(f"missing explicit runtime contract for project {project_id!r}") from exc


def ai_worker_cap(project_id: str, global_limit: int, contracts: dict | None = None) -> int:
    cap = int(project_contract(project_id, contracts).get("ai_worker_cap", 1))
    return max(0, min(max(0, int(global_limit)), cap))


def autonomy_policy(contracts: dict | None = None) -> dict:
    data = contracts or load_contracts()
    defaults = dict((data.get("defaults") or {}).get("autonomy") or {})
    projects = {}
    for project_id, contract in data["projects"].items():
        cfg = dict(defaults)
        cfg.update(dict(contract.get("autonomy") or {}))
        projects[project_id] = cfg
    return {"schema_version": 1, "default": defaults, "projects": projects}


def init_tables(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS project_state_receipts(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            phase TEXT NOT NULL DEFAULT '',
            action TEXT NOT NULL DEFAULT '',
            commit_sha TEXT NOT NULL DEFAULT '',
            ci_status TEXT NOT NULL DEFAULT '',
            blocker TEXT NOT NULL DEFAULT '',
            next_gate TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT '',
            observed_at TEXT NOT NULL,
            evidence_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        )"""
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS project_state_receipts_project_id "
        "ON project_state_receipts(project_id,id DESC)"
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS resource_leases(
            project_id TEXT NOT NULL,
            owner_id TEXT NOT NULL,
            pool TEXT NOT NULL,
            workload_class TEXT NOT NULL,
            cpu_soft_cores REAL NOT NULL DEFAULT 0,
            memory_soft_mb INTEGER NOT NULL DEFAULT 0,
            acquired_at TEXT NOT NULL,
            lease_until TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY(project_id,owner_id)
        )"""
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS resource_leases_pool_until "
        "ON resource_leases(pool,lease_until)"
    )


def _serialize_receipt_evidence(evidence: dict | None) -> str:
    if evidence is None:
        evidence = {}
    if not isinstance(evidence, dict):
        raise ValueError("receipt evidence must be an object")
    payload = json.dumps(
        evidence,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(payload.encode("utf-8")) > _MAX_RECEIPT_EVIDENCE_BYTES:
        raise ValueError(
            f"receipt evidence exceeds {_MAX_RECEIPT_EVIDENCE_BYTES} UTF-8 bytes"
        )
    return payload


def _receipt_payload(row) -> dict | None:
    if not row:
        return None
    item = dict(row)
    raw_evidence = item.pop("evidence_json") or "{}"
    try:
        evidence = json.loads(raw_evidence)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(evidence, dict):
        return None
    item["evidence"] = evidence
    return item


def record_receipt(
    connection: sqlite3.Connection,
    project_id: str,
    *,
    phase: str = "",
    action: str = "",
    commit_sha: str = "",
    ci_status: str = "",
    blocker: str = "",
    next_gate: str = "",
    source: str = "",
    observed_at: str | None = None,
    evidence: dict | None = None,
) -> dict:
    project_contract(project_id)
    ci_status = str(ci_status or "").lower()
    if ci_status not in {"", "queued", "in_progress", "success", "failure", "cancelled", "skipped"}:
        raise ValueError("unsupported ci_status")
    observed_at = str(observed_at or _now())
    created_at = _now()
    evidence_json = _serialize_receipt_evidence(evidence)
    cursor = connection.execute(
        """INSERT INTO project_state_receipts(
            project_id,phase,action,commit_sha,ci_status,blocker,next_gate,source,
            observed_at,evidence_json,created_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (
            str(project_id),
            str(phase or "")[:240],
            str(action or "")[:1000],
            str(commit_sha or "")[:80],
            ci_status,
            str(blocker or "")[:1000],
            str(next_gate or "")[:1000],
            str(source or "")[:300],
            observed_at,
            evidence_json,
            created_at,
        ),
    )
    row = connection.execute(
        "SELECT * FROM project_state_receipts WHERE id=?", (cursor.lastrowid,)
    ).fetchone()
    return _receipt_payload(row)


def latest_receipts(connection: sqlite3.Connection) -> dict[str, dict]:
    rows = connection.execute(
        """SELECT r.* FROM project_state_receipts r
           JOIN (
             SELECT project_id,MAX(id) AS id
             FROM project_state_receipts GROUP BY project_id
           ) latest ON latest.id=r.id"""
    ).fetchall()
    return {str(row["project_id"]): _receipt_payload(row) for row in rows}


def receipt_coverage(
    connection: sqlite3.Connection,
    project_ids,
    *,
    max_age_seconds: int = 24 * 3600,
    now_value: str | None = None,
) -> dict:
    """Return a fail-closed freshness audit for active project state receipts."""
    project_ids = sorted({str(project_id).strip() for project_id in project_ids if str(project_id).strip()})
    max_age_seconds = max(60, int(max_age_seconds or 24 * 3600))
    try:
        reference = datetime.fromisoformat(str(now_value)).astimezone(timezone.utc) if now_value else datetime.now(timezone.utc)
    except Exception as exc:
        raise ValueError("invalid receipt coverage reference time") from exc
    receipts = latest_receipts(connection)
    missing = []
    invalid = []
    stale = []
    current = []
    receipt_ids = {}
    for project_id in project_ids:
        if project_id not in receipts:
            missing.append(project_id)
            continue
        receipt = receipts.get(project_id)
        if not receipt:
            invalid.append(project_id)
            continue
        try:
            observed = datetime.fromisoformat(str(receipt.get("observed_at") or "")).astimezone(timezone.utc)
        except Exception:
            invalid.append(project_id)
            continue
        age_seconds = max(0, int((reference - observed).total_seconds()))
        receipt_ids[project_id] = receipt.get("id")
        if age_seconds > max_age_seconds:
            stale.append({"project_id": project_id, "age_seconds": age_seconds, "receipt_id": receipt.get("id")})
        else:
            current.append(project_id)
    return {
        "ready": not missing and not invalid and not stale,
        "time": reference.isoformat(),
        "max_age_seconds": max_age_seconds,
        "project_count": len(project_ids),
        "current_count": len(current),
        "current": current,
        "missing": missing,
        "invalid": invalid,
        "stale": stale,
        "receipt_ids": receipt_ids,
    }


def apply_receipt(project: dict, receipt: dict | None) -> dict:
    if not receipt:
        project["state_source"] = "registry"
        project["execution_state"] = {"available": False}
        return project
    if receipt.get("phase"):
        project["phase"] = receipt["phase"]
    if receipt.get("next_gate"):
        project["next_step"] = receipt["next_gate"]
    status = "blocked" if receipt.get("blocker") else (receipt.get("ci_status") or "observed")
    project["state_source"] = "evidence_receipt"
    project["execution_state"] = {
        "available": True,
        "status": status,
        "last_action": receipt.get("action") or "",
        "commit_sha": receipt.get("commit_sha") or "",
        "ci_status": receipt.get("ci_status") or "",
        "blocker": receipt.get("blocker") or "",
        "next_gate": receipt.get("next_gate") or "",
        "source": receipt.get("source") or "",
        "observed_at": receipt.get("observed_at"),
        "receipt_id": receipt.get("id"),
    }
    return project


def _lease_payload(row) -> dict | None:
    if not row:
        return None
    item = dict(row)
    try:
        item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
    except Exception:
        item["metadata"] = {}
        item.pop("metadata_json", None)
    return item


def _cleanup_expired(connection: sqlite3.Connection, now_value: str | None = None) -> None:
    connection.execute("DELETE FROM resource_leases WHERE lease_until<=?", (now_value or _now(),))


def acquire_resource(
    connection: sqlite3.Connection,
    project_id: str,
    owner_id: str,
    *,
    lease_seconds: int = 1800,
    metadata: dict | None = None,
) -> dict:
    contracts = load_contracts()
    contract = project_contract(project_id, contracts)
    compute = dict(contract.get("compute") or {})
    pool_name = str(compute.get("pool") or "")
    pool = contracts["resource_pools"][pool_name]
    slots = int(pool.get("slots") or 0)
    if slots <= 0:
        return {"acquired": False, "reason": "resource_pool_disabled", "project_id": project_id, "pool": pool_name}
    lease_seconds = max(60, min(6 * 3600, int(lease_seconds or 1800)))
    now_dt = datetime.now(timezone.utc)
    ts = now_dt.isoformat()
    until = (now_dt + timedelta(seconds=lease_seconds)).isoformat()
    _cleanup_expired(connection, ts)
    existing = connection.execute(
        "SELECT * FROM resource_leases WHERE project_id=? AND owner_id=?",
        (project_id, owner_id),
    ).fetchone()
    if existing:
        connection.execute(
            "UPDATE resource_leases SET lease_until=?,metadata_json=? WHERE project_id=? AND owner_id=?",
            (
                until,
                json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))[:4000],
                project_id,
                owner_id,
            ),
        )
        row = connection.execute(
            "SELECT * FROM resource_leases WHERE project_id=? AND owner_id=?",
            (project_id, owner_id),
        ).fetchone()
        return {"acquired": True, "renewed": True, "lease": _lease_payload(row)}
    used = int(
        connection.execute(
            "SELECT COUNT(*) FROM resource_leases WHERE pool=? AND lease_until>?",
            (pool_name, ts),
        ).fetchone()[0]
    )
    if used >= slots:
        holders = [
            _lease_payload(row)
            for row in connection.execute(
                "SELECT * FROM resource_leases WHERE pool=? AND lease_until>? ORDER BY acquired_at",
                (pool_name, ts),
            ).fetchall()
        ]
        return {
            "acquired": False,
            "reason": "resource_pool_busy",
            "project_id": project_id,
            "pool": pool_name,
            "capacity": slots,
            "holders": holders,
        }
    connection.execute(
        """INSERT INTO resource_leases(
            project_id,owner_id,pool,workload_class,cpu_soft_cores,memory_soft_mb,
            acquired_at,lease_until,metadata_json
        ) VALUES(?,?,?,?,?,?,?,?,?)""",
        (
            project_id,
            str(owner_id)[:200],
            pool_name,
            str(compute.get("class") or "unknown"),
            float(compute.get("cpu_soft_cores") or 0),
            int(compute.get("memory_soft_mb") or 0),
            ts,
            until,
            json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))[:4000],
        ),
    )
    row = connection.execute(
        "SELECT * FROM resource_leases WHERE project_id=? AND owner_id=?",
        (project_id, owner_id),
    ).fetchone()
    return {"acquired": True, "renewed": False, "lease": _lease_payload(row)}


def release_resource(connection: sqlite3.Connection, project_id: str, owner_id: str) -> dict:
    cursor = connection.execute(
        "DELETE FROM resource_leases WHERE project_id=? AND owner_id=?",
        (project_id, owner_id),
    )
    return {"released": cursor.rowcount == 1}


def resource_status(connection: sqlite3.Connection) -> dict:
    contracts = load_contracts()
    ts = _now()
    _cleanup_expired(connection, ts)
    rows = connection.execute(
        "SELECT * FROM resource_leases WHERE lease_until>? ORDER BY pool,acquired_at", (ts,)
    ).fetchall()
    leases = [_lease_payload(row) for row in rows]
    by_pool = {}
    for pool_name, cfg in contracts["resource_pools"].items():
        holders = [x for x in leases if x["pool"] == pool_name]
        by_pool[pool_name] = {
            "capacity": int(cfg.get("slots") or 0),
            "used": len(holders),
            "available": max(0, int(cfg.get("slots") or 0) - len(holders)),
            "holders": holders,
        }
    return {"time": ts, "pools": by_pool, "leases": leases}

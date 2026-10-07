#!/usr/bin/env python3
"""Fail-closed coherence audit for zCloud project registration surfaces."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _duplicates(values: list[str]) -> list[str]:
    seen: set[str] = set()
    dupes: set[str] = set()
    for value in values:
        if value in seen:
            dupes.add(value)
        seen.add(value)
    return sorted(dupes)


def audit_project_registry(
    projects: Any,
    contracts: Any,
    vps_policy: Any,
    queue_seed: Any,
) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []

    if not isinstance(projects, list):
        return {"state": "incoherent", "errors": [{"code": "projects_not_list"}]}
    if not isinstance(contracts, dict) or not isinstance(contracts.get("projects"), dict):
        return {"state": "incoherent", "errors": [{"code": "contracts_projects_not_object"}]}
    if not isinstance(vps_policy, dict) or not isinstance(vps_policy.get("projects"), list):
        return {"state": "incoherent", "errors": [{"code": "vps_projects_not_list"}]}
    if not isinstance(queue_seed, list):
        return {"state": "incoherent", "errors": [{"code": "queue_seed_not_list"}]}

    defaults = contracts.get("defaults", {})
    if not isinstance(defaults, dict):
        errors.append({"code": "invalid_contract_defaults"})
        defaults = {}

    project_ids = [row.get("id") for row in projects if isinstance(row, dict)]
    if len(project_ids) != len(projects) or any(not isinstance(value, str) or not value for value in project_ids):
        errors.append({"code": "invalid_project_id"})
        project_ids = [value for value in project_ids if isinstance(value, str) and value]

    duplicate_projects = _duplicates(project_ids)
    if duplicate_projects:
        errors.append({"code": "duplicate_project_id", "project_ids": duplicate_projects})

    registry_ids = set(project_ids)
    contract_ids = set(contracts["projects"])
    missing_contracts = sorted(registry_ids - contract_ids)
    unexpected_contracts = sorted(contract_ids - registry_ids)
    if missing_contracts:
        errors.append({"code": "missing_runtime_contract", "project_ids": missing_contracts})
    if unexpected_contracts:
        errors.append({"code": "orphan_runtime_contract", "project_ids": unexpected_contracts})

    vps_projects = vps_policy["projects"]
    if any(not isinstance(value, str) or not value for value in vps_projects):
        errors.append({"code": "invalid_vps_project_id"})
        vps_projects = [value for value in vps_projects if isinstance(value, str) and value]
    duplicate_vps_projects = _duplicates(vps_projects)
    if duplicate_vps_projects:
        errors.append({"code": "duplicate_vps_project_id", "project_ids": duplicate_vps_projects})

    vps_ids = set(vps_projects)
    missing_vps = sorted(registry_ids - vps_ids)
    unexpected_vps = sorted(vps_ids - registry_ids)
    if missing_vps:
        errors.append({"code": "missing_vps_policy_registration", "project_ids": missing_vps})
    if unexpected_vps:
        errors.append({"code": "orphan_vps_policy_registration", "project_ids": unexpected_vps})

    queue_ids: list[str] = []
    queue_projects: list[str] = []
    eligible_queued_projects: set[str] = set()
    for row in queue_seed:
        if not isinstance(row, dict):
            errors.append({"code": "invalid_queue_row"})
            continue
        queue_id = row.get("queue_id")
        project_id = row.get("project_id")
        if not isinstance(queue_id, str) or not queue_id:
            errors.append({"code": "invalid_queue_id"})
        else:
            queue_ids.append(queue_id)
        if not isinstance(project_id, str) or not project_id:
            errors.append({"code": "invalid_queue_project_id"})
            continue
        queue_projects.append(project_id)
        if project_id not in registry_ids:
            errors.append({"code": "unknown_queue_project", "project_ids": [project_id]})
        if row.get("eligible") is True and row.get("status") == "queued":
            eligible_queued_projects.add(project_id)

    duplicate_queue_ids = _duplicates(queue_ids)
    if duplicate_queue_ids:
        errors.append({"code": "duplicate_queue_id", "queue_ids": duplicate_queue_ids})

    required_seed_projects: set[str] = set()
    for project_id, contract in contracts["projects"].items():
        if not isinstance(contract, dict):
            errors.append({"code": "invalid_runtime_contract", "project_ids": [project_id]})
            continue
        autonomy = contract.get("autonomy")
        if not isinstance(autonomy, dict):
            autonomy = {}
        ai_worker_cap = contract.get("ai_worker_cap", defaults.get("ai_worker_cap", 0))
        if (
            not isinstance(ai_worker_cap, int)
            or isinstance(ai_worker_cap, bool)
            or ai_worker_cap < 0
        ):
            errors.append({"code": "invalid_ai_worker_cap", "project_ids": [project_id]})
            continue
        if (
            contract.get("queue_mode") == "execution"
            and autonomy.get("auto_start") is True
            and ai_worker_cap > 0
        ):
            required_seed_projects.add(project_id)

    missing_queue_seed = sorted(required_seed_projects - eligible_queued_projects)
    if missing_queue_seed:
        errors.append({"code": "autostart_project_without_runnable_seed", "project_ids": missing_queue_seed})

    return {
        "state": "coherent" if not errors else "incoherent",
        "project_count": len(registry_ids),
        "runtime_contract_count": len(contract_ids),
        "vps_policy_project_count": len(vps_ids),
        "queue_seed_project_count": len(set(queue_projects)),
        "autostart_execution_projects": sorted(required_seed_projects),
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--projects", type=Path, default=Path("projects.json"))
    parser.add_argument("--contracts", type=Path, default=Path("project-contracts.json"))
    parser.add_argument("--vps-policy", type=Path, default=Path("vps-execution-policy.json"))
    parser.add_argument("--queue-seed", type=Path, default=Path("portfolio_queue.seed.json"))
    args = parser.parse_args()

    try:
        result = audit_project_registry(
            _load_json(args.projects),
            _load_json(args.contracts),
            _load_json(args.vps_policy),
            _load_json(args.queue_seed),
        )
    except (OSError, json.JSONDecodeError) as exc:
        result = {"state": "incoherent", "errors": [{"code": "input_unreadable", "detail": type(exc).__name__}]}

    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["state"] == "coherent" else 2


if __name__ == "__main__":
    raise SystemExit(main())

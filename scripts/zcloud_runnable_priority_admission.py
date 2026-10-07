#!/usr/bin/env python3
"""Side-effect-free scheduler admission policy: priority only applies to runnable work."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACTS = ROOT / "project-contracts.json"
DEFAULT_RESOURCE_POLICY = ROOT / "resource-policy.json"
POLICY_SCHEMA_VERSION = 1
EVIDENCE_SCHEMA_VERSION = 1
MAX_EVIDENCE_BYTES = 256 * 1024
MAX_PROJECTS = 256

# Must stay numerically aligned with enhancements.PRIORITY_WEIGHTS, which is
# the existing dashboard/systemd resource-control contract.
PRIORITY_WEIGHT = {
    "background": 100,
    "normal": 400,
    "high": 800,
    "turbo": 3000,
}


class AdmissionError(ValueError):
    """Raised when scheduler evidence is not safe to rank."""


def _read_json(path: Path, *, max_bytes: int = MAX_EVIDENCE_BYTES) -> dict[str, Any]:
    if path.is_symlink():
        raise AdmissionError("symlink_input_forbidden")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise AdmissionError("input_unreadable") from exc
    if len(raw) > max_bytes:
        raise AdmissionError("input_too_large")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdmissionError("input_invalid_json") from exc
    if not isinstance(payload, dict):
        raise AdmissionError("input_not_object")
    return payload


def _load_contracts(path: Path) -> dict[str, Any]:
    payload = _read_json(path)
    projects = payload.get("projects")
    if not isinstance(projects, dict):
        raise AdmissionError("contracts_projects_missing")
    return payload


def _validate_resource_policy(
    resource_policy: dict[str, Any],
    *,
    contracts: dict[str, Any],
) -> None:
    projects = contracts.get("projects")
    if not isinstance(projects, dict):
        raise AdmissionError("contracts_projects_missing")
    unknown_projects = set(resource_policy) - set(projects)
    if unknown_projects:
        raise AdmissionError("resource_policy_project_unknown")
    for project_id, entry in resource_policy.items():
        if not isinstance(entry, dict) or set(entry) != {"priority"}:
            raise AdmissionError("resource_policy_entry_invalid")
        priority = entry.get("priority")
        if not isinstance(priority, str) or priority not in PRIORITY_WEIGHT:
            raise AdmissionError("resource_policy_priority_invalid")


def _priority_for(
    project_id: str,
    contracts: dict[str, Any],
    resource_policy: dict[str, Any],
) -> tuple[str, int, str]:
    projects = contracts.get("projects")
    project = projects.get(project_id) if isinstance(projects, dict) else None
    if not isinstance(project, dict):
        raise AdmissionError("project_unknown")
    compute = project.get("compute")
    if not isinstance(compute, dict):
        raise AdmissionError("compute_contract_missing")
    baseline = compute.get("priority")
    if not isinstance(baseline, str) or baseline not in PRIORITY_WEIGHT:
        raise AdmissionError("priority_invalid")

    override = resource_policy.get(project_id)
    if override is None:
        return baseline, PRIORITY_WEIGHT[baseline], "project_contract"
    priority = override["priority"]
    return priority, PRIORITY_WEIGHT[priority], "resource_policy"


def rank_runnable_projects(
    evidence: dict[str, Any],
    *,
    contracts: dict[str, Any],
    resource_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resource_policy = {} if resource_policy is None else resource_policy
    if not isinstance(resource_policy, dict):
        raise AdmissionError("resource_policy_invalid")
    _validate_resource_policy(resource_policy, contracts=contracts)

    allowed_top_level = {"schema_version", "projects"}
    if set(evidence) - allowed_top_level:
        raise AdmissionError("evidence_unknown_top_level_field")
    schema_version = evidence.get("schema_version")
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version != EVIDENCE_SCHEMA_VERSION
    ):
        raise AdmissionError("evidence_schema_invalid")

    rows = evidence.get("projects")
    if not isinstance(rows, list):
        raise AdmissionError("evidence_projects_missing")
    if len(rows) > MAX_PROJECTS:
        raise AdmissionError("evidence_project_limit")

    seen: set[str] = set()
    candidates: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []

    for row in rows:
        if not isinstance(row, dict):
            raise AdmissionError("evidence_row_invalid")
        allowed = {"project_id", "runnable", "safety_admitted"}
        if set(row) - allowed:
            raise AdmissionError("evidence_unknown_field")

        project_id = row.get("project_id")
        if not isinstance(project_id, str) or not project_id:
            raise AdmissionError("project_id_invalid")
        if project_id != project_id.strip():
            raise AdmissionError("project_id_invalid")
        if project_id in seen:
            raise AdmissionError("project_duplicate")
        seen.add(project_id)

        runnable = row.get("runnable")
        safety_admitted = row.get("safety_admitted")
        if not isinstance(runnable, bool):
            raise AdmissionError("runnable_invalid")
        if not isinstance(safety_admitted, bool):
            raise AdmissionError("safety_admitted_invalid")

        declared_priority, weight, priority_source = _priority_for(
            project_id,
            contracts,
            resource_policy,
        )
        effective = bool(runnable and safety_admitted)
        item = {
            "project_id": project_id,
            "declared_priority": declared_priority,
            "priority_source": priority_source,
            "priority_weight": weight if effective else 0,
            "runnable": runnable,
            "safety_admitted": safety_admitted,
            "priority_effective": effective,
        }
        if effective:
            candidates.append(item)
        else:
            excluded.append(item)

    candidates.sort(key=lambda item: (-item["priority_weight"], item["project_id"]))
    excluded.sort(key=lambda item: item["project_id"])

    return {
        "schema_version": POLICY_SCHEMA_VERSION,
        "policy": "runnable-priority-admission-v1",
        "candidates": candidates,
        "excluded": excluded,
        "guardrails": {
            "priority_requires_runnable": True,
            "priority_requires_safety_admission": True,
            "priority_may_create_runnable_work": False,
            "priority_may_bypass_blockers": False,
            "priority_may_bypass_resource_guards": False,
            "priority_source_is_runtime_persistent_policy": True,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Rank only already-runnable zCloud projects by current resource priority"
    )
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--contracts", type=Path, default=DEFAULT_CONTRACTS)
    parser.add_argument("--resource-policy", type=Path, default=DEFAULT_RESOURCE_POLICY)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        contracts = _load_contracts(args.contracts)
        result = rank_runnable_projects(
            _read_json(args.evidence),
            contracts=contracts,
            resource_policy=_read_json(args.resource_policy),
        )
    except AdmissionError as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        else:
            print(f"ZCLOUD_RUNNABLE_PRIORITY_BLOCKED error={exc}")
        return 2

    if args.json:
        print(json.dumps({"ok": True, "result": result}, sort_keys=True))
    else:
        ordered = ",".join(item["project_id"] for item in result["candidates"]) or "-"
        print(
            "ZCLOUD_RUNNABLE_PRIORITY_GREEN",
            f"candidates={len(result['candidates'])}",
            f"excluded={len(result['excluded'])}",
            f"order={ordered}",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

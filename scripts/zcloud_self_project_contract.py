#!/usr/bin/env python3
"""Fail-closed audit for zCloud's self-project orchestration contract."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[1]
DEFAULT_PROJECTS = REPO / "projects.json"
DEFAULT_CONTRACTS = REPO / "project-contracts.json"
DEFAULT_RESOURCES = REPO / "resource-policy.json"
DEFAULT_LAYOUT = REPO / "project-layout.json"
DEFAULT_LEGACY_AUTONOMY = REPO / "autonomy-policy.json"
CANONICAL_REPO_URL = "https://github.com/zennay/zcloud"


class ContractError(RuntimeError):
    pass


def _load_json(path: Path) -> Any:
    if path.is_symlink():
        raise ContractError(f"{path.name}: symlink input refused")
    if not path.is_file():
        raise ContractError(f"{path.name}: regular file required")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"{path.name}: invalid JSON: {exc}") from exc


def _repo_url(value: object) -> str:
    return str(value or "").strip().lower().removesuffix("/").removesuffix(".git")


def audit(
    projects_path: Path = DEFAULT_PROJECTS,
    contracts_path: Path = DEFAULT_CONTRACTS,
    resources_path: Path = DEFAULT_RESOURCES,
    layout_path: Path = DEFAULT_LAYOUT,
    legacy_autonomy_path: Path = DEFAULT_LEGACY_AUTONOMY,
) -> dict[str, Any]:
    errors: list[str] = []
    checks: list[str] = []

    try:
        projects = _load_json(projects_path)
        contracts = _load_json(contracts_path)
        resources = _load_json(resources_path)
        layout = _load_json(layout_path)
        legacy_autonomy = _load_json(legacy_autonomy_path)
    except ContractError as exc:
        return {"ok": False, "errors": [str(exc)], "checks": checks}

    if not isinstance(projects, list):
        errors.append("projects.json: top-level array required")
        project_rows: list[dict[str, Any]] = []
    else:
        project_rows = [row for row in projects if isinstance(row, dict)]
        if len(project_rows) != len(projects):
            errors.append("projects.json: every project entry must be an object")

    ids = [str(row.get("id") or "") for row in project_rows]
    duplicate_ids = sorted({pid for pid in ids if pid and ids.count(pid) > 1})
    if duplicate_ids:
        errors.append(f"projects.json: duplicate project ids: {','.join(duplicate_ids)}")

    cloud_rows = [row for row in project_rows if row.get("id") == "cloud"]
    if len(cloud_rows) != 1:
        errors.append(f"projects.json: expected exactly one cloud project, found {len(cloud_rows)}")
        cloud_registry: dict[str, Any] = {}
    else:
        cloud_registry = cloud_rows[0]
        checks.append("registry:cloud-present")

    if cloud_registry:
        if _repo_url(cloud_registry.get("repo_url")) != CANONICAL_REPO_URL:
            errors.append("projects.json: cloud repo_url must point to Zennay/zCloud")
        else:
            checks.append("registry:canonical-repo")
        for field in ("notion_url", "handoff_url"):
            value = str(cloud_registry.get(field) or "")
            if not value.startswith("https://app.notion.com/"):
                errors.append(f"projects.json: cloud {field} must be a canonical Notion URL")
            else:
                checks.append(f"registry:{field}")
        if str(cloud_registry.get("status") or "") != "active":
            errors.append("projects.json: cloud must remain an active project")
        else:
            checks.append("registry:active")

    if not isinstance(contracts, dict):
        errors.append("project-contracts.json: top-level object required")
        contract_projects: dict[str, Any] = {}
        pools: dict[str, Any] = {}
    else:
        if contracts.get("schema_version") != 1:
            errors.append("project-contracts.json: schema_version must be 1")
        contract_projects = contracts.get("projects") if isinstance(contracts.get("projects"), dict) else {}
        pools = contracts.get("resource_pools") if isinstance(contracts.get("resource_pools"), dict) else {}

    cloud_contract = contract_projects.get("cloud")
    if not isinstance(cloud_contract, dict):
        errors.append("project-contracts.json: cloud runtime contract required")
        cloud_contract = {}
    else:
        checks.append("runtime:cloud-present")

    for key, value in {
        "queue_mode": "execution",
        "lane_profile": "platform",
        "ai_worker_cap": 1,
    }.items():
        if cloud_contract.get(key) != value:
            errors.append(f"project-contracts.json: cloud {key} must equal {value!r}")
        else:
            checks.append(f"runtime:{key}")

    autonomy = cloud_contract.get("autonomy")
    if not isinstance(autonomy, dict):
        errors.append("project-contracts.json: cloud autonomy object required")
        autonomy = {}
    if autonomy.get("mode") != "zcloud_stopgate":
        errors.append("project-contracts.json: cloud autonomy.mode must be zcloud_stopgate")
    else:
        checks.append("runtime:zcloud-stopgate")
    if autonomy.get("auto_start") is not True:
        errors.append("project-contracts.json: cloud autonomy.auto_start must be true; stopgate owns continuation")
    else:
        checks.append("runtime:stopgate-owned-autostart")

    compute = cloud_contract.get("compute")
    if not isinstance(compute, dict):
        errors.append("project-contracts.json: cloud compute object required")
        compute = {}
    if compute.get("class") != "control-plane":
        errors.append("project-contracts.json: cloud compute.class must be control-plane")
    if compute.get("pool") != "protected":
        errors.append("project-contracts.json: cloud compute.pool must be protected")
    if compute.get("protected") is not True:
        errors.append("project-contracts.json: cloud compute.protected must be true")
    protected_pool = pools.get("protected")
    if not isinstance(protected_pool, dict) or not isinstance(protected_pool.get("slots"), int) or protected_pool["slots"] < 1:
        errors.append("project-contracts.json: protected pool must retain at least one slot")
    else:
        checks.append("runtime:protected-capacity")

    if not isinstance(resources, dict):
        errors.append("resource-policy.json: top-level object required")
        cloud_resource = None
    else:
        cloud_resource = resources.get("cloud")
    if not isinstance(cloud_resource, dict):
        errors.append("resource-policy.json: cloud resource policy required")
    elif cloud_resource.get("priority") != compute.get("priority"):
        errors.append("resource-policy.json: cloud priority must match runtime contract")
    else:
        checks.append("resource:priority-aligned")

    if not isinstance(layout, dict):
        errors.append("project-layout.json: top-level object required")
    else:
        order = layout.get("order")
        archived = layout.get("archived")
        if not isinstance(order, list) or not all(isinstance(item, str) for item in order):
            errors.append("project-layout.json: order must be a string array")
        else:
            cloud_count = order.count("cloud")
            if cloud_count != 1:
                errors.append(f"project-layout.json: cloud must appear exactly once in order, found {cloud_count}")
            else:
                checks.append("layout:cloud-visible")
        if not isinstance(archived, list) or not all(isinstance(item, str) for item in archived):
            errors.append("project-layout.json: archived must be a string array")
        elif "cloud" in archived:
            errors.append("project-layout.json: cloud control-plane must not be archived")
        else:
            checks.append("layout:cloud-not-archived")

    legacy_projects = legacy_autonomy.get("projects") if isinstance(legacy_autonomy, dict) else None
    if not isinstance(legacy_projects, dict):
        errors.append("autonomy-policy.json: projects object required")
    elif "cloud" in legacy_projects:
        errors.append("autonomy-policy.json: cloud must not drift into legacy autonomy truth")
    else:
        checks.append("legacy:no-cloud-drift")

    return {"ok": not errors, "errors": errors, "checks": checks}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--projects", type=Path, default=DEFAULT_PROJECTS)
    parser.add_argument("--contracts", type=Path, default=DEFAULT_CONTRACTS)
    parser.add_argument("--resources", type=Path, default=DEFAULT_RESOURCES)
    parser.add_argument("--layout", type=Path, default=DEFAULT_LAYOUT)
    parser.add_argument("--legacy-autonomy", type=Path, default=DEFAULT_LEGACY_AUTONOMY)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = audit(args.projects, args.contracts, args.resources, args.layout, args.legacy_autonomy)
    if args.json:
        print(json.dumps(result, sort_keys=True))
    elif result["ok"]:
        print("ZCLOUD_SELF_PROJECT_CONTRACT_GREEN=1")
        print(f"checks={len(result['checks'])}")
    else:
        for error in result["errors"]:
            print(f"ERROR: {error}", file=sys.stderr)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

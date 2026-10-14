#!/usr/bin/env python3
"""Validate and report candidate per-project min/target/max CPU profiles.

This module is intentionally side-effect free. It does not mutate the canonical
project contract, scheduler, SQLite state, systemd controls, or live services.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
MAX_INPUT_BYTES = 256 * 1024
MAX_CPU_CORES = 64.0
ROOT_KEYS = {"schema_version", "description", "projects"}
PROFILE_KEYS = {"minimum_cpu_cores", "target_cpu_cores", "maximum_cpu_cores"}


class PolicyError(ValueError):
    pass


def _exact_int(value: Any) -> bool:
    return type(value) is int


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PolicyError(f"{name}: finite number required")
    number = float(value)
    if not math.isfinite(number) or number < 0 or number > MAX_CPU_CORES:
        raise PolicyError(f"{name}: expected 0..{MAX_CPU_CORES:g}")
    return number


def _load_json(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise PolicyError(f"{path}: symlink inputs are refused")
    if not path.is_file():
        raise PolicyError(f"{path}: regular file required")
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise PolicyError(f"{path}: input exceeds {MAX_INPUT_BYTES} bytes")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PolicyError(f"{path}: unreadable JSON") from exc
    if not isinstance(value, dict):
        raise PolicyError(f"{path}: object root required")
    return value


def build_report(contracts: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    if not _exact_int(contracts.get("schema_version")) or contracts.get("schema_version") != 1:
        raise PolicyError("project-contracts.json: exact schema_version 1 required")
    contract_projects = contracts.get("projects")
    if not isinstance(contract_projects, dict) or not contract_projects:
        raise PolicyError("project-contracts.json.projects: non-empty object required")

    unknown_root = set(policy) - ROOT_KEYS
    missing_root = ROOT_KEYS - set(policy)
    if unknown_root or missing_root:
        raise PolicyError(
            f"resource profile root keys mismatch: missing={sorted(missing_root)} unknown={sorted(unknown_root)}"
        )
    if not _exact_int(policy.get("schema_version")) or policy.get("schema_version") != SCHEMA_VERSION:
        raise PolicyError(f"resource profile: exact schema_version {SCHEMA_VERSION} required")
    if not isinstance(policy.get("description"), str) or not policy["description"].strip():
        raise PolicyError("resource profile.description: non-empty string required")

    policy_projects = policy.get("projects")
    if not isinstance(policy_projects, dict):
        raise PolicyError("resource profile.projects: object required")
    expected = set(contract_projects)
    actual = set(policy_projects)
    if expected != actual:
        raise PolicyError(
            f"resource profile projects mismatch: missing={sorted(expected-actual)} unknown={sorted(actual-expected)}"
        )

    rows: list[dict[str, Any]] = []
    for project_id in sorted(expected):
        if not isinstance(project_id, str) or not project_id or project_id != project_id.strip():
            raise PolicyError("project IDs must be non-empty canonical strings")
        contract = contract_projects[project_id]
        if not isinstance(contract, dict):
            raise PolicyError(f"{project_id}: project contract object required")
        compute = contract.get("compute")
        if not isinstance(compute, dict):
            raise PolicyError(f"{project_id}: compute contract object required")

        profile = policy_projects[project_id]
        if not isinstance(profile, dict):
            raise PolicyError(f"{project_id}: resource profile object required")
        if set(profile) != PROFILE_KEYS:
            raise PolicyError(
                f"{project_id}: profile keys must be exactly {sorted(PROFILE_KEYS)}"
            )

        minimum = _number(profile["minimum_cpu_cores"], f"{project_id}.minimum_cpu_cores")
        target = _number(profile["target_cpu_cores"], f"{project_id}.target_cpu_cores")
        maximum = _number(profile["maximum_cpu_cores"], f"{project_id}.maximum_cpu_cores")
        if not minimum <= target <= maximum:
            raise PolicyError(f"{project_id}: require minimum <= target <= maximum")

        legacy_target = _number(compute.get("cpu_soft_cores"), f"{project_id}.compute.cpu_soft_cores")
        if target != legacy_target:
            raise PolicyError(
                f"{project_id}: target must equal current cpu_soft_cores during compatibility phase"
            )

        protected = compute.get("protected")
        if type(protected) is not bool:
            raise PolicyError(f"{project_id}.compute.protected: exact boolean required")
        pool = compute.get("pool")
        if not isinstance(pool, str) or not pool:
            raise PolicyError(f"{project_id}.compute.pool: non-empty string required")

        if pool == "disabled" and (minimum != 0 or target != 0 or maximum != 0):
            raise PolicyError(f"{project_id}: disabled pool requires zero CPU profile")
        if protected and minimum < legacy_target:
            raise PolicyError(
                f"{project_id}: protected minimum may not weaken current cpu_soft_cores reserve"
            )

        rows.append(
            {
                "project_id": project_id,
                "pool": pool,
                "protected": protected,
                "minimum_cpu_cores": minimum,
                "target_cpu_cores": target,
                "maximum_cpu_cores": maximum,
                "legacy_target_cpu_cores": legacy_target,
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "contract": "resource-profile-policy-v1",
        "status": "compatible",
        "project_count": len(rows),
        "profiles": rows,
        "runtime_mutation": False,
        "integration_state": "candidate_only",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contracts", type=Path, default=Path("project-contracts.json"))
    parser.add_argument(
        "--profiles", type=Path, default=Path("resource-profile-policy.v1.json")
    )
    parser.add_argument("--require-compatible", action="store_true")
    args = parser.parse_args()
    try:
        report = build_report(_load_json(args.contracts), _load_json(args.profiles))
    except PolicyError as exc:
        print(json.dumps({"schema_version": SCHEMA_VERSION, "status": "invalid", "reason": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    if args.require_compatible and report["status"] != "compatible":
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

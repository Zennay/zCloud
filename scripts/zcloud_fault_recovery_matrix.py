#!/usr/bin/env python3
"""Validate and optionally execute the bounded zCloud fault-recovery matrix."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
MAX_INPUT_BYTES = 64 * 1024
EXPECTED_SCENARIOS = {
    "firefox_runtime_loss": (
        "python_unittest",
        "tests.test_worker_progress_watchdog.WorkerWatchdogRuntimeRecoveryTests.test_critical_memory_lost_runtime_restarts_firefox",
    ),
    "zcloud_service_restart": (
        "python_unittest",
        "tests.test_runner_smoke.RunnerSmokeTests.test_reconnect_adopts_and_persists_conversation",
    ),
    "stale_resource_lease": (
        "python_unittest",
        "tests.test_project_runtime.ProjectRuntimeTests.test_expired_heavy_lease_is_recovered_after_owner_crash",
    ),
}
EXPECTED_ADDITIONAL = ["tests/test_firefox_recovery_contract.js"]
ROOT_KEYS = {"schema_version", "contract", "description", "scenarios", "additional_contracts"}
SCENARIO_KEYS = {"id", "kind", "selector", "invariant"}


class MatrixError(ValueError):
    pass


def _load(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise MatrixError("symlink matrix input refused")
    if not path.is_file():
        raise MatrixError("regular matrix file required")
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise MatrixError("matrix input too large")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MatrixError("matrix JSON unreadable") from exc
    if not isinstance(value, dict):
        raise MatrixError("matrix root must be an object")
    return value


def validate(matrix: dict[str, Any], root: Path) -> dict[str, Any]:
    if set(matrix) != ROOT_KEYS:
        raise MatrixError("matrix root keys must match the v1 contract exactly")
    if type(matrix.get("schema_version")) is not int or matrix["schema_version"] != SCHEMA_VERSION:
        raise MatrixError("exact integer schema_version 1 required")
    if matrix.get("contract") != "fault-recovery-matrix-v1":
        raise MatrixError("unknown matrix contract")
    if not isinstance(matrix.get("description"), str) or not matrix["description"].strip():
        raise MatrixError("non-empty description required")

    scenarios = matrix.get("scenarios")
    if not isinstance(scenarios, list) or len(scenarios) != len(EXPECTED_SCENARIOS):
        raise MatrixError("exact canonical scenario set required")

    seen: set[str] = set()
    selectors: list[str] = []
    for scenario in scenarios:
        if not isinstance(scenario, dict) or set(scenario) != SCENARIO_KEYS:
            raise MatrixError("scenario keys must match the v1 contract exactly")
        scenario_id = scenario.get("id")
        if not isinstance(scenario_id, str) or scenario_id in seen:
            raise MatrixError("scenario IDs must be unique canonical strings")
        seen.add(scenario_id)
        expected = EXPECTED_SCENARIOS.get(scenario_id)
        if expected is None:
            raise MatrixError(f"unknown scenario: {scenario_id}")
        if (scenario.get("kind"), scenario.get("selector")) != expected:
            raise MatrixError(f"{scenario_id}: selector is not the canonical bounded simulation")
        if not isinstance(scenario.get("invariant"), str) or not scenario["invariant"].strip():
            raise MatrixError(f"{scenario_id}: invariant required")
        selectors.append(scenario["selector"])

    if seen != set(EXPECTED_SCENARIOS):
        raise MatrixError("canonical scenario coverage incomplete")

    additional = matrix.get("additional_contracts")
    if additional != EXPECTED_ADDITIONAL:
        raise MatrixError("additional recovery contracts must match the canonical bounded set")
    for rel in additional:
        path = root / rel
        if path.is_symlink() or not path.is_file():
            raise MatrixError(f"required recovery contract missing or unsafe: {rel}")

    return {
        "schema_version": SCHEMA_VERSION,
        "contract": "fault-recovery-matrix-v1",
        "status": "valid",
        "scenario_count": len(selectors),
        "scenario_ids": sorted(seen),
        "selectors": selectors,
        "additional_contracts": list(additional),
        "live_mutation": False,
    }


def execute(report: dict[str, Any], root: Path) -> dict[str, Any]:
    completed: list[str] = []
    for scenario_id, selector in zip(
        [s["id"] for s in json.loads((root / "fault-recovery-matrix.v1.json").read_text(encoding="utf-8"))["scenarios"]],
        report["selectors"],
    ):
        subprocess.run(
            [sys.executable, "-m", "unittest", "-v", selector],
            cwd=root,
            check=True,
        )
        completed.append(scenario_id)
    subprocess.run(["node", "tests/test_firefox_recovery_contract.js"], cwd=root, check=True)
    result = dict(report)
    result["status"] = "green"
    result["executed_scenarios"] = completed
    result["additional_contracts_executed"] = list(report["additional_contracts"])
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix", type=Path, default=Path("fault-recovery-matrix.v1.json"))
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--require-green", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        report = validate(_load(args.matrix), root)
        if args.execute:
            report = execute(report, root)
    except (MatrixError, subprocess.CalledProcessError, OSError) as exc:
        print(json.dumps({"schema_version": SCHEMA_VERSION, "status": "invalid", "reason": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    if args.require_green and report["status"] != "green":
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

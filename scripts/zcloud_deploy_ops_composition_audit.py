#!/usr/bin/env python3
"""Fail-closed validator for deploy-ops partial-owner composition requirements."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "ops" / "deploy_ops_composition_requirements.json"


def load_contract(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"contract must be a regular file: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("contract root must be an object")
    if data.get("version") != 1:
        raise ValueError("unsupported contract version")

    pairs = data.get("pairs")
    if not isinstance(pairs, list) or not pairs:
        raise ValueError("pairs must be a non-empty list")

    seen_workflows: set[str] = set()
    seen_owners: set[int] = set()
    for idx, pair in enumerate(pairs):
        if not isinstance(pair, dict):
            raise ValueError(f"pair[{idx}] must be an object")
        workflow = pair.get("workflow")
        newer = pair.get("newer_owner")
        older = pair.get("older_owner")
        if not isinstance(workflow, str) or not workflow.startswith(".github/workflows/"):
            raise ValueError(f"pair[{idx}] has invalid workflow")
        if workflow in seen_workflows:
            raise ValueError(f"duplicate workflow: {workflow}")
        seen_workflows.add(workflow)

        for label, owner in (("newer_owner", newer), ("older_owner", older)):
            if not isinstance(owner, int) or owner <= 0:
                raise ValueError(f"pair[{idx}] has invalid {label}")
            if owner in seen_owners:
                raise ValueError(f"owner #{owner} appears in multiple composition pairs")
            seen_owners.add(owner)

        if newer == older:
            raise ValueError(f"pair[{idx}] owners must differ")
        if pair.get("compose_into_single_owner_before_retirement") is not True:
            raise ValueError(f"pair[{idx}] must fail closed on retirement")

        for key in ("required_newer_controls", "required_older_controls"):
            controls = pair.get(key)
            if not isinstance(controls, list) or not controls:
                raise ValueError(f"pair[{idx}] {key} must be non-empty")
            if any(not isinstance(item, str) or not item.strip() for item in controls):
                raise ValueError(f"pair[{idx}] {key} contains an invalid control")
            if len(set(controls)) != len(controls):
                raise ValueError(f"pair[{idx}] {key} contains duplicates")

    retirement_rule = data.get("retirement_rule")
    if not isinstance(retirement_rule, str) or "may be closed as a duplicate" not in retirement_rule:
        raise ValueError("retirement_rule must explicitly block premature duplicate closure")
    return data


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--workflow")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    data = load_contract(args.contract)
    pairs = data["pairs"]
    if args.workflow:
        pairs = [pair for pair in pairs if pair["workflow"] == args.workflow]
        if len(pairs) != 1:
            raise SystemExit(f"workflow is not registered exactly once: {args.workflow}")

    payload = {
        "ok": True,
        "version": data["version"],
        "pair_count": len(data["pairs"]),
        "pairs": pairs,
        "serialized_writer_gate": data["serialized_writer_gate"],
    }
    if args.json:
        print(json.dumps(payload, sort_keys=True))
    else:
        for pair in pairs:
            print(
                f"{pair['workflow']}: newer=#{pair['newer_owner']} "
                f"older=#{pair['older_owner']} compose-before-retirement"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

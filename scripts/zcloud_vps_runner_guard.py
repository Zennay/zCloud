#!/usr/bin/env python3
"""Fail-closed identity guard for the permanent zCloud VPS deploy lane."""

from __future__ import annotations

import argparse
import json
import os
import socket
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "vps-execution-policy.json"


def _short_host(value: str) -> str:
    return (value or "").strip().lower().split(".", 1)[0]


def validate_runner(
    policy_path: Path = DEFAULT_POLICY,
    *,
    hostname: str | None = None,
    runner_name: str | None = None,
) -> dict:
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    runner = policy.get("runner") or {}
    expected_host = _short_host(str(runner.get("host") or ""))
    actual_host = _short_host(hostname if hostname is not None else socket.gethostname())
    actual_runner = (
        runner_name if runner_name is not None else os.environ.get("RUNNER_NAME", "")
    ).strip()
    forbidden = [
        str(token).strip().lower()
        for token in (runner.get("forbidden_name_tokens") or [])
        if str(token).strip()
    ]

    errors: list[str] = []
    if not expected_host:
        errors.append("policy_host_missing")
    elif actual_host != expected_host:
        errors.append("host_mismatch")

    if not actual_runner:
        errors.append("runner_name_missing")
    else:
        lowered = actual_runner.lower()
        for token in forbidden:
            if token in lowered:
                errors.append(f"forbidden_runner_name:{token}")

    return {
        "ok": not errors,
        "expected_host": expected_host,
        "actual_host": actual_host,
        "runner_name": actual_runner,
        "forbidden_name_tokens": forbidden,
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate that a zCloud VPS workflow is running on the permanent runner"
    )
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    result = validate_runner(args.policy)
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        status = "GREEN" if result["ok"] else "BLOCKED"
        print(
            "ZCLOUD_VPS_RUNNER_IDENTITY_" + status,
            f"host={result['actual_host']}",
            f"runner={result['runner_name']}",
            f"errors={','.join(result['errors']) or 'none'}",
        )
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

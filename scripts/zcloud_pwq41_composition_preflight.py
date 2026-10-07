#!/usr/bin/env python3
"""Read-only composition preflight for the stale PWQ-41 / PR #580 intent.

The original #580 branch is far behind main and overlaps the separately-owned
push-all recovery slice.  This scanner does not mutate runtime state or owned
workflow files.  It answers one bounded question: which non-overlapping #580
safety semantics are already present in the checkout that a future restack
would use?
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

SCHEMA = "zcloud-pwq41-composition-preflight-v1"

REQUIREMENTS = (
    {
        "id": "health_long_replacement_lease",
        "path": "scripts/zcloud_healthcheck.py",
        "tokens": (
            "LONG_PENDING_COMMAND_SECONDS",
            "ZCLOUD_HEALTH_LONG_PENDING_COMMAND_SECONDS",
            'if action in {"new_chat", "drain"}',
            "stale_after_seconds",
        ),
    },
    {
        "id": "health_runner_target_identity",
        "path": "scripts/zcloud_healthcheck.py",
        "tokens": (
            "store_target_ids",
            "runner-target references unknown base project",
            "runner-target key mismatch",
            "project_id identity mismatch",
        ),
    },
    {
        "id": "watchdog_canonical_worker_identity",
        "path": "scripts/zcloud_worker_watchdog.py",
        "tokens": (
            'item.get("base_project_id")',
            'if "::w" in project',
            "encoded_slot",
            "return project",
        ),
    },
    {
        "id": "watchdog_long_replacement_lease",
        "path": "scripts/zcloud_worker_watchdog.py",
        "tokens": (
            "LONG_STALE_PENDING_COMMAND_SECONDS",
            "ZCLOUD_WATCHDOG_LONG_STALE_PENDING_COMMAND_SECONDS",
            "SELECT id,action,created_at FROM runner_commands",
            'if action in {"new_chat", "drain"}',
        ),
    },
    {
        "id": "restart_long_replacement_lease",
        "path": ".github/workflows/zcloud-restart-workers.yml",
        "tokens": (
            "ORDINARY_COMMAND_STALE_SECONDS = 120",
            "LONG_COMMAND_STALE_SECONDS",
            "ZCLOUD_RUNNER_COMMAND_LONG_STALE_SECONDS",
            'SELECT id,action,created_at FROM runner_commands',
        ),
    },
    {
        "id": "server_long_replacement_lease",
        "path": "server.py",
        "tokens": (
            "RUNNER_COMMAND_LONG_STALE_SECONDS",
            "ZCLOUD_RUNNER_COMMAND_LONG_STALE_SECONDS",
            "SELECT id,action,created_at FROM runner_commands",
            "else RUNNER_COMMAND_STALE_SECONDS",
        ),
    },
)

SHARED_OWNERSHIP = (
    ".github/workflows/push-all-workers-now.yml",
    "tests/test_push_all_workers_workflow.py",
)


def _read(root: Path, relative: str) -> str:
    path = root / relative
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def audit(root: Path) -> dict:
    checks = []
    for requirement in REQUIREMENTS:
        text = _read(root, requirement["path"])
        missing = [token for token in requirement["tokens"] if token not in text]
        checks.append(
            {
                "id": requirement["id"],
                "path": requirement["path"],
                "present": bool(text) and not missing,
                "missing_tokens": missing,
            }
        )
    return {
        "schema": SCHEMA,
        "ready": all(item["present"] for item in checks),
        "checks": checks,
        "shared_paths_not_claimed": list(SHARED_OWNERSHIP),
    }


def git_head(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    value = result.stdout.strip()
    return value or None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only PWQ-41/#580 current-checkout composition preflight"
    )
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--expected-head")
    parser.add_argument("--require-ready", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    result = audit(root)
    result["head_sha"] = git_head(root)
    result["expected_head"] = args.expected_head or None
    result["head_matches"] = (
        True
        if not args.expected_head
        else result["head_sha"] == args.expected_head
    )
    if not result["head_matches"]:
        result["ready"] = False

    if args.json:
        print(json.dumps(result, sort_keys=True, indent=2))
    else:
        print("PWQ41_COMPOSITION_" + ("READY" if result["ready"] else "INCOMPLETE"))
        for item in result["checks"]:
            state = "PRESENT" if item["present"] else "MISSING"
            print(f"{state} {item['id']} {item['path']}")
        print("SHARED_PATHS_NOT_CLAIMED=" + ",".join(result["shared_paths_not_claimed"]))

    return 2 if args.require_ready and not result["ready"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Repair legacy/runtime project catalog gaps without replacing runtime-owned state."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_ROOT = Path(os.environ.get("ZCLOUD_ROOT", "/home/ubuntu/zennay-cloud"))
DEFAULT_DB = Path(os.environ.get("ZCLOUD_DB", str(DEFAULT_ROOT / "history.db")))
DEFAULT_LOCK = Path(os.environ.get(
    "ZCLOUD_PROJECT_SCHEMA_LOCK",
    str(Path.home() / ".local/state/zcloud/runtime-project-schema.lock"),
))


class RepairError(RuntimeError):
    pass


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def repair_projects(projects, candidate_projects=None):
    if not isinstance(projects, list):
        raise RepairError("projects.json must be an array")
    repaired = json.loads(json.dumps(projects, ensure_ascii=False))
    changed = []
    live_ids = set()

    for item in repaired:
        if not isinstance(item, dict):
            continue
        project_id = str(item.get("id") or "").strip()
        if project_id:
            if project_id in live_ids:
                raise RepairError(f"duplicate runtime project id {project_id!r}")
            live_ids.add(project_id)
        revision = item.get("milestone_revision")
        if isinstance(revision, str) and revision.strip():
            continue
        if not project_id:
            raise RepairError("cannot repair milestone_revision for project without id")
        item["milestone_revision"] = "runtime-import-v1"
        changed.append(project_id)

    if candidate_projects is not None:
        if not isinstance(candidate_projects, list):
            raise RepairError("candidate projects.json must be an array")
        candidate_ids = set()
        for item in candidate_projects:
            if not isinstance(item, dict):
                raise RepairError("candidate projects.json entries must be objects")
            project_id = str(item.get("id") or "").strip()
            if not project_id:
                raise RepairError("candidate project without id")
            if project_id in candidate_ids:
                raise RepairError(f"duplicate candidate project id {project_id!r}")
            candidate_ids.add(project_id)
            if project_id in live_ids:
                continue

            added = json.loads(json.dumps(item, ensure_ascii=False))
            revision = added.get("milestone_revision")
            if not isinstance(revision, str) or not revision.strip():
                added["milestone_revision"] = "runtime-import-v1"
            repaired.append(added)
            live_ids.add(project_id)
            changed.append(project_id)

    return repaired, changed


def validate_candidate(*, validator: Path, staged_projects: Path, root: Path, candidate: Path, db: Path) -> None:
    command = [
        sys.executable,
        str(validator),
        "--projects", str(staged_projects),
        "--layout", str(root / "project-layout.json"),
        "--resource-policy", str(root / "resource-policy.json"),
        "--project-contracts", str(candidate / "project-contracts.json"),
        "--server", str(candidate / "server.py"),
        "--enhancements", str(candidate / "enhancements.py"),
        "--db", str(db),
        "--json",
    ]
    result = subprocess.run(command, text=True, capture_output=True, check=False, timeout=30)
    if result.returncode != 0:
        detail = (result.stdout or result.stderr or "config validation failed").strip()
        raise RepairError("candidate config validation rejected repaired catalog: " + detail[:2000])


def atomic_write(path: Path, value: bytes) -> None:
    staged = path.with_name(f".{path.name}.schema-repair.{os.getpid()}")
    try:
        staged.write_bytes(value)
        os.chmod(staged, 0o644)
        os.replace(staged, path)
    finally:
        try:
            staged.unlink()
        except FileNotFoundError:
            pass


def write_audit(db: Path, old_value, new_value, changed: list[str]) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    detail = "runtime project catalog repair: " + ",".join(changed)
    with sqlite3.connect(db, timeout=5) as conn:
        conn.execute(
            "INSERT INTO config_audit("
            "ts,actor,config_key,target,old_value_json,new_value_json,result,detail"
            ") VALUES(?,?,?,?,?,?,?,?)",
            (
                ts,
                "github-actions-vps-deploy",
                "project.catalog",
                "portfolio",
                canonical_json(old_value),
                canonical_json(new_value),
                "succeeded",
                detail[:300],
            ),
        )


def repair_live(*, root: Path, candidate: Path, db: Path, validator: Path, lock_path: Path) -> dict:
    projects_path = root / "projects.json"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock_handle:
        fcntl.flock(lock_handle, fcntl.LOCK_EX)

        before_bytes = projects_path.read_bytes()
        before = json.loads(before_bytes.decode("utf-8"))
        candidate_projects = json.loads((candidate / "projects.json").read_text(encoding="utf-8"))
        after, changed = repair_projects(before, candidate_projects)
        if not changed:
            return {
                "ok": True,
                "changed": False,
                "projects": [],
                "sha256": sha256_bytes(before_bytes),
            }

        staged = root / f".projects.json.validate.{os.getpid()}"
        staged_bytes = (json.dumps(after, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        try:
            staged.write_bytes(staged_bytes)
            validate_candidate(
                validator=validator,
                staged_projects=staged,
                root=root,
                candidate=candidate,
                db=db,
            )
        finally:
            try:
                staged.unlink()
            except FileNotFoundError:
                pass

        # Fail closed if another writer changed the runtime catalog while validation ran.
        if projects_path.read_bytes() != before_bytes:
            raise RepairError("projects.json changed concurrently; refusing schema repair")

        atomic_write(projects_path, staged_bytes)
        try:
            write_audit(db, before, after, changed)
        except Exception as exc:
            atomic_write(projects_path, before_bytes)
            raise RepairError(f"config audit failed; restored original projects.json: {exc}") from exc

        return {
            "ok": True,
            "changed": True,
            "projects": changed,
            "sha256": sha256_bytes(staged_bytes),
        }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Repair legacy zCloud runtime project schema")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--validator", type=Path)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    validator = args.validator or (args.candidate / "scripts" / "zcloud_config_validate.py")
    try:
        result = repair_live(
            root=args.root.resolve(),
            candidate=args.candidate.resolve(),
            db=args.db.resolve(),
            validator=validator.resolve(),
            lock_path=args.lock.expanduser().resolve(),
        )
    except Exception as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        else:
            print(f"RUNTIME_PROJECT_SCHEMA_REPAIR_BLOCKED: {exc}")
        return 2

    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        state = "changed" if result["changed"] else "no_change"
        print(f"RUNTIME_PROJECT_SCHEMA_REPAIR={state} sha256={result['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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


LEGACY_RUNTIME_CONTRACT_FIELDS = ("queue_mode", "lane_profile")


class RepairError(RuntimeError):
    pass


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def repair_projects(projects, candidate_projects=None, *, worker_role_ids=()):
    """Add new portfolio cards without overriding live metadata; demote explicit worker roles."""
    if not isinstance(projects, list):
        raise RepairError("projects.json must be an array")
    role_ids = set(worker_role_ids)
    candidate_by_id = {}
    candidate_order = []
    if candidate_projects is not None:
        if not isinstance(candidate_projects, list):
            raise RepairError("candidate projects.json must be an array")
        for item in candidate_projects:
            if not isinstance(item, dict):
                raise RepairError("candidate projects.json entries must be objects")
            project_id = str(item.get("id") or "").strip()
            if not project_id:
                raise RepairError("candidate project without id")
            if project_id in candidate_by_id or project_id in role_ids:
                raise RepairError(f"duplicate/colliding candidate project id {project_id!r}")
            candidate_by_id[project_id] = item
            candidate_order.append(project_id)

    repaired = []
    changed = []
    changed_ids = set()
    live_ids = set()

    def mark_changed(project_id: str) -> None:
        if project_id not in changed_ids:
            changed.append(project_id)
            changed_ids.add(project_id)

    for item in json.loads(json.dumps(projects, ensure_ascii=False)):
        if not isinstance(item, dict):
            raise RepairError("runtime project entry must be an object")
        project_id = str(item.get("id") or "").strip()
        if not project_id:
            raise RepairError("runtime project without id")
        if project_id in live_ids:
            raise RepairError(f"duplicate runtime project id {project_id!r}")
        live_ids.add(project_id)
        if project_id in role_ids:
            mark_changed(project_id)
            continue  # Only explicit candidate worker roles may lose portfolio-card status.

        candidate_item = candidate_by_id.get(project_id)
        if candidate_item is not None:
            for field in LEGACY_RUNTIME_CONTRACT_FIELDS:
                if field in item and field not in candidate_item:
                    item.pop(field, None)
                    mark_changed(project_id)

        revision = item.get("milestone_revision")
        if not isinstance(revision, str) or not revision.strip():
            item["milestone_revision"] = "runtime-import-v1"
            mark_changed(project_id)
        repaired.append(item)

    for project_id in candidate_order:
        if project_id in live_ids:
            continue
        added = json.loads(json.dumps(candidate_by_id[project_id], ensure_ascii=False))
        revision = added.get("milestone_revision")
        if not isinstance(revision, str) or not revision.strip():
            added["milestone_revision"] = "runtime-import-v1"
        repaired.append(added)
        mark_changed(project_id)

    return repaired, changed


def repair_layout(layout, worker_role_ids):
    """Keep user ordering and archives intact, removing only demoted worker roles."""
    if not isinstance(layout, dict):
        raise RepairError("project-layout.json must be an object")
    repaired = json.loads(json.dumps(layout, ensure_ascii=False))
    role_ids = set(worker_role_ids)
    for key in ("order", "archived"):
        entries = repaired.get(key, [])
        if not isinstance(entries, list):
            raise RepairError(f"project-layout.json.{key} must be an array")
        repaired[key] = [pid for pid in entries if pid not in role_ids]
    return repaired


def validate_candidate(*, validator: Path, staged_projects: Path, root: Path, candidate: Path, db: Path, staged_layout: Path | None = None) -> None:
    command = [
        sys.executable,
        str(validator),
        "--projects", str(staged_projects),
        "--layout", str(staged_layout or root / "project-layout.json"),
        "--resource-policy", str(root / "resource-policy.json"),
        "--project-contracts", str(candidate / "project-contracts.json"),
        "--server", str(candidate / "server.py"),
        "--enhancements", str(candidate / "enhancements.py"),
        "--db", str(db),
        "--json",
    ]
    candidate_modes = candidate / "worker-modes.json"
    if candidate_modes.is_file():
        command.extend(["--worker-modes", str(candidate_modes)])
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


def write_audit(db: Path, old_value, new_value, changed: list[str], *, old_layout=None, new_layout=None) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    updates = [
        ("project.catalog", old_value, new_value, "runtime project catalog repair: " + ",".join(changed)),
    ]
    if old_layout is not None and old_layout != new_layout:
        updates.append(("project.layout", old_layout, new_layout, "demote worker role from portfolio layout"))
    with sqlite3.connect(db, timeout=5) as conn:
        for key, old, new, detail in updates:
            conn.execute(
                "INSERT INTO config_audit("
                "ts,actor,config_key,target,old_value_json,new_value_json,result,detail"
                ") VALUES(?,?,?,?,?,?,?,?)",
                (
                    ts,
                    "github-actions-vps-deploy",
                    key,
                    "portfolio",
                    canonical_json(old),
                    canonical_json(new),
                    "succeeded",
                    detail[:300],
                ),
            )


def repair_live(*, root: Path, candidate: Path, db: Path, validator: Path, lock_path: Path) -> dict:
    projects_path = root / "projects.json"
    layout_path = root / "project-layout.json"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock_handle:
        fcntl.flock(lock_handle, fcntl.LOCK_EX)

        before_bytes = projects_path.read_bytes()
        before = json.loads(before_bytes.decode("utf-8"))
        layout_before_bytes = layout_path.read_bytes()
        layout_before = json.loads(layout_before_bytes.decode("utf-8"))
        candidate_projects = json.loads((candidate / "projects.json").read_text(encoding="utf-8"))
        candidate_modes = candidate / "worker-modes.json"
        roles = json.loads(candidate_modes.read_text(encoding="utf-8")) if candidate_modes.is_file() else []
        if not isinstance(roles, list) or any(
            not isinstance(role, dict) or not isinstance(role.get("id"), str) or not role["id"].strip()
            for role in roles
        ):
            raise RepairError("candidate worker-modes.json must contain valid role IDs")
        role_ids = [role["id"] for role in roles]
        if len(set(role_ids)) != len(role_ids):
            raise RepairError("duplicate candidate worker role ID")

        after, changed = repair_projects(before, candidate_projects, worker_role_ids=role_ids)
        layout_after = repair_layout(layout_before, role_ids)
        layout_changed = layout_before != layout_after
        if not changed and not layout_changed:
            return {"ok": True, "changed": False, "projects": [], "sha256": sha256_bytes(before_bytes)}

        staged = root / f".projects.json.validate.{os.getpid()}"
        staged_layout = root / f".project-layout.json.validate.{os.getpid()}"
        staged_bytes = (json.dumps(after, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        staged_layout_bytes = (json.dumps(layout_after, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        try:
            staged.write_bytes(staged_bytes)
            staged_layout.write_bytes(staged_layout_bytes)
            validate_candidate(
                validator=validator, staged_projects=staged, staged_layout=staged_layout,
                root=root, candidate=candidate, db=db,
            )
        finally:
            for path in (staged, staged_layout):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass

        if projects_path.read_bytes() != before_bytes or layout_path.read_bytes() != layout_before_bytes:
            raise RepairError("project registry/layout changed concurrently; refusing schema repair")
        try:
            if changed:
                atomic_write(projects_path, staged_bytes)
            if layout_changed:
                atomic_write(layout_path, staged_layout_bytes)
            write_audit(db, before, after, changed, old_layout=layout_before, new_layout=layout_after)
        except Exception as exc:
            atomic_write(projects_path, before_bytes)
            atomic_write(layout_path, layout_before_bytes)
            raise RepairError(f"config repair failed; restored original project registry/layout: {exc}") from exc

        return {
            "ok": True,
            "changed": True,
            "projects": changed,
            "layout_changed": layout_changed,
            "sha256": sha256_bytes(staged_bytes if changed else before_bytes),
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

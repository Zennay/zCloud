#!/usr/bin/env python3
"""Fail-closed schema validation for zCloud config and worker state."""
from __future__ import annotations

import argparse
import ast
import json
import re
import sqlite3
import sys
from pathlib import Path

ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"{path.name}: invalid JSON: {exc}") from exc


def literal_assignment(path: Path, name: str):
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except Exception as exc:
        raise ValueError(f"{path.name}: cannot parse runtime contract: {exc}") from exc
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    try:
                        return ast.literal_eval(node.value)
                    except Exception as exc:
                        raise ValueError(
                            f"{path.name}: {name} must remain a literal for config validation"
                        ) from exc
    raise ValueError(f"{path.name}: runtime contract {name} not found")


def add(errors: list[str], condition: bool, message: str) -> None:
    if not condition:
        errors.append(message)


def validate_milestones(items, where: str, errors: list[str]) -> None:
    add(errors, isinstance(items, list) and bool(items), f"{where}: milestones must be a non-empty list")
    if not isinstance(items, list):
        return
    titles = set()
    for index, item in enumerate(items):
        prefix = f"{where}.milestones[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{prefix}: must be an object")
            continue
        title = item.get("title")
        add(errors, isinstance(title, str) and bool(title.strip()), f"{prefix}.title: non-empty string required")
        if isinstance(title, str):
            add(errors, title not in titles, f"{prefix}.title: duplicate title {title!r}")
            titles.add(title)
        add(errors, isinstance(item.get("done"), bool), f"{prefix}.done: boolean required")
        progress = item.get("progress")
        add(
            errors,
            isinstance(progress, (int, float)) and not isinstance(progress, bool) and 0 <= progress <= 100,
            f"{prefix}.progress: number in range 0..100 required",
        )


def validate_projects(data, errors: list[str]) -> set[str]:
    add(errors, isinstance(data, list) and bool(data), "projects.json: non-empty array required")
    if not isinstance(data, list):
        return set()
    ids: set[str] = set()
    for index, project in enumerate(data):
        prefix = f"projects[{index}]"
        if not isinstance(project, dict):
            errors.append(f"{prefix}: must be an object")
            continue
        pid = project.get("id")
        add(errors, isinstance(pid, str) and bool(ID_RE.fullmatch(pid or "")), f"{prefix}.id: lowercase slug required")
        if isinstance(pid, str):
            add(errors, pid not in ids, f"{prefix}.id: duplicate project id {pid!r}")
            ids.add(pid)
        for key in ("name", "status", "milestone_revision", "progress_basis"):
            add(
                errors,
                isinstance(project.get(key), str) and bool(project.get(key).strip()),
                f"{prefix}.{key}: non-empty string required",
            )
        validate_milestones(project.get("milestones"), prefix, errors)
        override = project.get("progress_override")
        if override is not None:
            add(
                errors,
                isinstance(override, (int, float)) and not isinstance(override, bool) and 0 <= override <= 100,
                f"{prefix}.progress_override: number in range 0..100 required",
            )
        components = project.get("components", [])
        add(errors, isinstance(components, list), f"{prefix}.components: list required when present")
        if isinstance(components, list):
            component_ids = set()
            for cindex, component in enumerate(components):
                cp = f"{prefix}.components[{cindex}]"
                if not isinstance(component, dict):
                    errors.append(f"{cp}: must be an object")
                    continue
                cid = component.get("id")
                add(errors, isinstance(cid, str) and bool(ID_RE.fullmatch(cid or "")), f"{cp}.id: lowercase slug required")
                if isinstance(cid, str):
                    add(errors, cid not in component_ids, f"{cp}.id: duplicate component id {cid!r}")
                    component_ids.add(cid)
                add(errors, isinstance(component.get("name"), str) and bool(component.get("name").strip()), f"{cp}.name: non-empty string required")
                progress = component.get("progress")
                add(
                    errors,
                    isinstance(progress, (int, float)) and not isinstance(progress, bool) and 0 <= progress <= 100,
                    f"{cp}.progress: number in range 0..100 required",
                )
                validate_milestones(component.get("milestones"), cp, errors)
    return ids


def validate_layout(data, project_ids: set[str], errors: list[str]) -> None:
    if not isinstance(data, dict):
        errors.append("project-layout.json: object required")
        return
    unknown_keys = set(data) - {"order", "archived"}
    add(errors, not unknown_keys, f"project-layout.json: unsupported keys {sorted(unknown_keys)}")
    for key in ("order", "archived"):
        values = data.get(key, [])
        add(errors, isinstance(values, list), f"project-layout.json.{key}: list required")
        if not isinstance(values, list):
            continue
        add(errors, all(isinstance(x, str) for x in values), f"project-layout.json.{key}: string ids only")
        add(errors, len(values) == len(set(values)), f"project-layout.json.{key}: duplicate ids not allowed")
        unknown = sorted({x for x in values if isinstance(x, str)} - project_ids)
        add(errors, not unknown, f"project-layout.json.{key}: unknown project ids {unknown}")


def validate_resource_policy(
    data,
    project_ids: set[str],
    priorities: set[str],
    resource_projects: set[str],
    errors: list[str],
) -> None:
    if not isinstance(data, dict):
        errors.append("resource-policy.json: object required")
        return
    for pid, cfg in data.items():
        add(errors, pid in project_ids, f"resource-policy.json: unknown project {pid!r}")
        add(errors, pid in resource_projects, f"resource-policy.json: project {pid!r} has no resource-control contract")
        if not isinstance(cfg, dict):
            errors.append(f"resource-policy.json.{pid}: object required")
            continue
        unknown = set(cfg) - {"priority"}
        add(errors, not unknown, f"resource-policy.json.{pid}: unsupported keys {sorted(unknown)}")
        priority = cfg.get("priority")
        add(errors, priority in priorities, f"resource-policy.json.{pid}.priority: expected one of {sorted(priorities)}")


def validate_worker_state(db_path: Path, project_ids: set[str], max_workers: int, errors: list[str]) -> dict:
    summary = {"checked": False, "targets": 0, "workers": 0}
    if not db_path.exists():
        errors.append("history.db: missing worker-state store")
        return summary
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)
        conn.row_factory = sqlite3.Row
        tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"runner_targets", "runner_workers"}.issubset(tables):
            errors.append("history.db: runner_targets/runner_workers tables required")
            conn.close()
            return summary
        targets = [dict(row) for row in conn.execute(
            "SELECT project_id,worker_count FROM runner_targets ORDER BY project_id"
        )]
        workers = [dict(row) for row in conn.execute(
            "SELECT project_id,worker_slot,desired_state FROM runner_workers ORDER BY project_id,worker_slot"
        )]
        conn.close()
        summary = {"checked": True, "targets": len(targets), "workers": len(workers)}
        desired = {"running", "paused", "draining"}
        by_project: dict[str, set[int]] = {}
        for worker in workers:
            pid = str(worker["project_id"])
            slot = int(worker["worker_slot"])
            by_project.setdefault(pid, set()).add(slot)
            add(errors, pid in project_ids or pid in ("portfolio-review","portfolio-worker"), f"history.db: unknown worker project {pid!r}")
            add(errors, 1 <= slot <= max_workers, f"history.db: worker slot out of range {pid}::{slot}")
            add(errors, worker["desired_state"] in desired, f"history.db: invalid desired_state for {pid}::{slot}")
        for target in targets:
            pid = str(target["project_id"])
            count = int(target["worker_count"])
            add(errors, pid in project_ids or pid in ("portfolio-review","portfolio-worker"), f"history.db: unknown runner target {pid!r}")
            add(errors, 1 <= count <= max_workers, f"history.db: worker_count out of range for {pid}: {count}")
            missing = [slot for slot in range(1, count + 1) if slot not in by_project.get(pid, set())]
            add(errors, not missing, f"history.db: missing configured worker slots for {pid}: {missing}")
    except Exception as exc:
        errors.append(f"history.db: worker-state validation failed: {exc}")
    return summary


def validate(
    *,
    projects_path: Path,
    layout_path: Path,
    resource_path: Path,
    server_path: Path,
    enhancements_path: Path,
    db_path: Path | None,
) -> dict:
    errors: list[str] = []
    try:
        max_workers = literal_assignment(server_path, "MAX_CHATGPT_WORKERS")
        priority_weights = literal_assignment(enhancements_path, "PRIORITY_WEIGHTS")
        project_units = literal_assignment(enhancements_path, "PROJECT_UNITS")
    except ValueError as exc:
        errors.append(str(exc))
        max_workers, priority_weights, project_units = 0, {}, {}

    add(errors, isinstance(max_workers, int) and 1 <= max_workers <= 64, "server.py: MAX_CHATGPT_WORKERS must be integer 1..64")
    add(
        errors,
        isinstance(priority_weights, dict) and bool(priority_weights)
        and all(isinstance(k, str) and isinstance(v, (int, float)) and v > 0 for k, v in priority_weights.items()),
        "enhancements.py: PRIORITY_WEIGHTS must be a non-empty positive numeric mapping",
    )
    add(
        errors,
        isinstance(project_units, dict) and bool(project_units)
        and all(isinstance(k, str) and isinstance(v, list) for k, v in project_units.items()),
        "enhancements.py: PROJECT_UNITS must be a non-empty mapping of project -> unit list",
    )

    try:
        projects = load_json(projects_path)
        layout = load_json(layout_path)
        resources = load_json(resource_path)
    except ValueError as exc:
        errors.append(str(exc))
        return {"ok": False, "errors": errors}

    project_ids = validate_projects(projects, errors)
    validate_layout(layout, project_ids, errors)
    validate_resource_policy(
        resources,
        project_ids,
        set(priority_weights) if isinstance(priority_weights, dict) else set(),
        set(project_units) if isinstance(project_units, dict) else set(),
        errors,
    )
    worker_state = (
        validate_worker_state(db_path, project_ids, max_workers, errors)
        if db_path is not None and isinstance(max_workers, int) and max_workers > 0
        else {"checked": False}
    )
    return {
        "ok": not errors,
        "errors": errors,
        "contracts": {
            "project_count": len(project_ids),
            "max_workers": max_workers,
            "priorities": sorted(priority_weights) if isinstance(priority_weights, dict) else [],
            "resource_projects": sorted(project_units) if isinstance(project_units, dict) else [],
            "worker_state": worker_state,
        },
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Validate zCloud config schema and worker settings")
    parser.add_argument("--projects", type=Path, required=True)
    parser.add_argument("--layout", type=Path, required=True)
    parser.add_argument("--resource-policy", type=Path, required=True)
    parser.add_argument("--server", type=Path, required=True)
    parser.add_argument("--enhancements", type=Path, required=True)
    parser.add_argument("--db", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result = validate(
        projects_path=args.projects.resolve(),
        layout_path=args.layout.resolve(),
        resource_path=args.resource_policy.resolve(),
        server_path=args.server.resolve(),
        enhancements_path=args.enhancements.resolve(),
        db_path=args.db.resolve() if args.db else None,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if result["ok"]:
            print("CONFIG_SCHEMA_GREEN")
        else:
            for error in result["errors"]:
                print("FAIL", error)
            print("CONFIG_SCHEMA_BLOCKED")
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Emit sanitized evidence for zCloud pre-change drift decisions.

The report intentionally excludes config contents, audit actors/details, database
rows outside the two mutable portfolio config keys, and all persistent secrets.
It is safe to attach to CI evidence for deploylane diagnosis.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path


CONFIG_AUDITS = {
    "projects.json": ("project.catalog", "portfolio"),
    "project-layout.json": ("project.layout", "portfolio"),
}


def sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_sha(value) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def audit_rows(db_path: Path, config_key: str, target: str, current) -> list[dict]:
    if not db_path.is_file():
        return []
    uri = f"file:{db_path}?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=4) as conn:
        rows = conn.execute(
            "SELECT id,ts,result,new_value_json FROM config_audit "
            "WHERE config_key=? AND target=? ORDER BY id DESC LIMIT 8",
            (config_key, target),
        ).fetchall()
    evidence = []
    for row_id, ts, result, raw_new in rows:
        try:
            value = json.loads(raw_new)
            semantic_sha = canonical_json_sha(value)
            matches_current = value == current
        except Exception:
            semantic_sha = None
            matches_current = False
        evidence.append({
            "id": int(row_id),
            "ts": str(ts),
            "result": str(result),
            "new_semantic_sha256": semantic_sha,
            "matches_current": bool(matches_current),
        })
    return evidence


def project_delta(left, right) -> dict:
    if not isinstance(left, list) or not isinstance(right, list):
        return {"comparable": False}
    def index(items):
        out = {}
        for item in items:
            if isinstance(item, dict) and str(item.get("id") or "").strip():
                out[str(item["id"])] = item
        return out
    old = index(left)
    new = index(right)
    changed = {}
    for project_id in sorted(set(old) & set(new)):
        keys = sorted(
            key for key in (set(old[project_id]) | set(new[project_id]))
            if old[project_id].get(key) != new[project_id].get(key)
        )
        if keys:
            changed[project_id] = keys
    return {
        "comparable": True,
        "added_ids": sorted(set(new) - set(old)),
        "removed_ids": sorted(set(old) - set(new)),
        "changed_fields": changed,
    }


def layout_delta(left, right) -> dict:
    if not isinstance(left, dict) or not isinstance(right, dict):
        return {"comparable": False}
    return {
        "comparable": True,
        "order_changed": left.get("order") != right.get("order"),
        "archived_changed": left.get("archived") != right.get("archived"),
        "left_order": list(left.get("order") or []),
        "right_order": list(right.get("order") or []),
        "left_archived": list(left.get("archived") or []),
        "right_archived": list(right.get("archived") or []),
    }


def git_state(repo: Path) -> dict:
    """Return sanitized candidate repository drift metadata without file names."""
    if not (repo / ".git").exists():
        return {"available": False, "reason": "git_metadata_missing"}

    def value(*args: str) -> str | None:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args],
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
        return proc.stdout.strip() if proc.returncode == 0 else None

    head = value("rev-parse", "HEAD")
    origin_main = value("rev-parse", "origin/main")
    merge_base = (
        value("merge-base", "HEAD", "origin/main")
        if head and origin_main
        else None
    )
    ahead = behind = None
    if head and origin_main:
        raw = value("rev-list", "--left-right", "--count", "origin/main...HEAD")
        if raw:
            try:
                behind_text, ahead_text = raw.split()
                behind = int(behind_text)
                ahead = int(ahead_text)
            except (TypeError, ValueError):
                ahead = behind = None
    status = value("status", "--porcelain=v1", "--untracked-files=normal")
    dirty_count = len(status.splitlines()) if status else 0
    branch = value("branch", "--show-current") or None
    return {
        "available": bool(head),
        "head": head,
        "origin_main": origin_main,
        "merge_base": merge_base,
        "ahead_of_origin_main": ahead,
        "behind_origin_main": behind,
        "dirty": dirty_count > 0,
        "dirty_count": dirty_count,
        "branch": branch,
    }


def repository_file_match(repo: Path, rel: str, target_sha: str | None) -> str | None:
    if not target_sha or not (repo / ".git").exists():
        return None
    history = subprocess.run(
        ["git", "-C", str(repo), "log", "--all", "--format=%H", "--", rel],
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )
    for commit in history.stdout.splitlines()[:512]:
        blob = subprocess.run(
            ["git", "-C", str(repo), "show", f"{commit}:{rel}"],
            capture_output=True,
            check=False,
            timeout=5,
        )
        if blob.returncode == 0 and hashlib.sha256(blob.stdout).hexdigest() == target_sha:
            return commit
    return None


def build_report(root: Path, state: Path, candidate: Path) -> dict:
    pointer = read_json(state / "last-known-good.json")
    snapshot = state / "snapshots" / str(pointer["snapshot_id"])
    manifest = read_json(snapshot / "manifest.json")
    lkg_created = parse_time(str(manifest["created_at"]))

    files: dict[str, dict] = {}
    for rel in ("projects.json", "project-layout.json"):
        live_path = root / rel
        lkg_path = snapshot / "files" / rel
        candidate_path = candidate / rel
        live = read_json(live_path)
        lkg = read_json(lkg_path)
        candidate_value = read_json(candidate_path)
        key, target = CONFIG_AUDITS[rel]
        rows = audit_rows(root / "history.db", key, target, live)
        for row in rows:
            try:
                row_time = parse_time(row["ts"])
                row["after_lkg"] = row_time > lkg_created
            except Exception:
                row["after_lkg"] = False
        delta_fn = project_delta if rel == "projects.json" else layout_delta
        files[rel] = {
            "live_sha256": sha256_file(live_path),
            "lkg_sha256": sha256_file(lkg_path),
            "candidate_sha256": sha256_file(candidate_path),
            "live_semantic_sha256": canonical_json_sha(live),
            "lkg_semantic_sha256": canonical_json_sha(lkg),
            "candidate_semantic_sha256": canonical_json_sha(candidate_value),
            "live_equals_lkg": live == lkg,
            "live_equals_candidate": live == candidate_value,
            "live_vs_lkg": delta_fn(lkg, live),
            "live_vs_candidate": delta_fn(candidate_value, live),
            "audits": rows,
        }

    server_live = root / "server.py"
    server_lkg = snapshot / "files" / "server.py"
    server_candidate = candidate / "server.py"
    server_live_sha = sha256_file(server_live)
    files["server.py"] = {
        "live_sha256": server_live_sha,
        "lkg_sha256": sha256_file(server_lkg),
        "candidate_sha256": sha256_file(server_candidate),
        "live_equals_lkg": (
            server_live_sha is not None
            and server_live_sha == sha256_file(server_lkg)
        ),
        "live_equals_candidate": (
            server_live_sha is not None
            and server_live_sha == sha256_file(server_candidate)
        ),
        "repository_match_commit": repository_file_match(
            candidate, "server.py", server_live_sha
        ),
    }
    return {
        "schema_version": 1,
        "snapshot_id": str(pointer["snapshot_id"]),
        "lkg_created_at": str(manifest["created_at"]),
        "lkg_git_head": str((manifest.get("git") or {}).get("head") or "") or None,
        "candidate_git": git_state(candidate),
        "files": files,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("/home/ubuntu/zennay-cloud"))
    parser.add_argument(
        "--state",
        type=Path,
        default=Path.home() / ".local/state/zcloud/recovery",
    )
    parser.add_argument("--candidate", type=Path, required=True)
    args = parser.parse_args()
    report = build_report(args.root.resolve(), args.state.resolve(), args.candidate.resolve())
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

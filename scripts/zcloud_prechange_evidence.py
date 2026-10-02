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
        files[rel] = {
            "live_sha256": sha256_file(live_path),
            "lkg_sha256": sha256_file(lkg_path),
            "candidate_sha256": sha256_file(candidate_path),
            "live_semantic_sha256": canonical_json_sha(live),
            "lkg_semantic_sha256": canonical_json_sha(lkg),
            "candidate_semantic_sha256": canonical_json_sha(candidate_value),
            "live_equals_lkg": live == lkg,
            "live_equals_candidate": live == candidate_value,
            "audits": rows,
        }

    server_live = root / "server.py"
    server_lkg = snapshot / "files" / "server.py"
    server_candidate = candidate / "server.py"
    files["server.py"] = {
        "live_sha256": sha256_file(server_live),
        "lkg_sha256": sha256_file(server_lkg),
        "candidate_sha256": sha256_file(server_candidate),
        "live_equals_lkg": (
            sha256_file(server_live) is not None
            and sha256_file(server_live) == sha256_file(server_lkg)
        ),
        "live_equals_candidate": (
            sha256_file(server_live) is not None
            and sha256_file(server_live) == sha256_file(server_candidate)
        ),
    }
    return {
        "schema_version": 1,
        "snapshot_id": str(pointer["snapshot_id"]),
        "lkg_created_at": str(manifest["created_at"]),
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

#!/usr/bin/env python3
"""Bounded read-only HaxLab telemetry projection for zCloud."""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


AUTONOMY_STATUS = Path("/var/lib/haxlab/state/autonomy-status.json")
CURRENT_CHAMPION = Path("/var/lib/haxlab/derived/champions/elite-player/current.json")
LIVE_CHAMPION = Path("/var/lib/haxlab/derived/champions/elite-player/live.json")
PIPELINE_SUMMARY = Path(
    "/var/lib/haxlab/derived/training/elite-player-champion-candidate/pipeline-summary.json"
)
REPLAY_DB = Path("/var/lib/haxlab/state/haxlab.sqlite3")
PRIVILEGED_JSON = frozenset({str(CURRENT_CHAMPION), str(LIVE_CHAMPION)})
SAFE_TOKEN = re.compile(r"[^A-Za-z0-9._:/-]+")


def _token(value: object, limit: int = 120) -> str | None:
    if value is None:
        return None
    text = SAFE_TOKEN.sub("_", str(value).strip())
    return text[:limit] or None


def _mtime_iso(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
    except OSError:
        return None


def _load_json(path: Path, *, allow_privileged: bool = False) -> tuple[dict[str, Any], str | None]:
    if path.is_symlink():
        return {}, "symlink_refused"
    if not path.is_file():
        return {}, "missing"
    raw: str
    try:
        raw = path.read_text(encoding="utf-8")
    except PermissionError:
        if not allow_privileged or str(path) not in PRIVILEGED_JSON:
            return {}, "permission_denied"
        proc = subprocess.run(
            ["sudo", "-n", "cat", "--", str(path)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=3,
            check=False,
        )
        if proc.returncode:
            return {}, "privileged_read_failed"
        raw = proc.stdout
    except OSError:
        return {}, "read_failed"
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}, "invalid_json"
    if not isinstance(value, dict):
        return {}, "object_required"
    return value, None


def _replay_snapshot(db_path: Path) -> dict[str, Any]:
    base: dict[str, Any] = {
        "available": False,
        "source": str(db_path),
        "observed_at": _mtime_iso(db_path),
    }
    if db_path.is_symlink():
        return {**base, "error": "symlink_refused"}
    if not db_path.is_file():
        return {**base, "error": "missing"}

    before = db_path.stat()
    uri = f"file:{db_path}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True, timeout=1) as conn:
            conn.execute("PRAGMA query_only=ON")
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            required = {"replay_analysis", "replay_processing"}
            if not required.issubset(tables):
                return {**base, "error": "required_tables_missing"}

            analysis_counts = {
                str(status or "unknown"): int(count)
                for status, count in conn.execute(
                    "SELECT status,COUNT(*) FROM replay_analysis GROUP BY status"
                ).fetchall()
            }
            processing_counts = {
                str(status or "unknown"): int(count)
                for status, count in conn.execute(
                    "SELECT status,COUNT(*) FROM replay_processing GROUP BY status"
                ).fetchall()
            }
            processed = conn.execute(
                """
                SELECT
                    COALESCE(SUM(duration_seconds),0),
                    COALESCE(SUM(total_frames),0),
                    COALESCE(SUM(decompressed_bytes),0)
                FROM replay_processing
                WHERE status='ok'
                """
            ).fetchone()
            analyzed = conn.execute(
                """
                SELECT
                    COALESCE(SUM(sampled_state_count),0),
                    COALESCE(SUM(raw_event_count),0),
                    COALESCE(SUM(tick_count),0)
                FROM replay_analysis
                WHERE status='ok'
                """
            ).fetchone()
            if conn.total_changes != 0:
                return {**base, "error": "unexpected_sqlite_write"}
    except sqlite3.Error:
        return {**base, "error": "sqlite_read_failed"}

    after = db_path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        return {**base, "error": "source_changed_during_read"}

    return {
        **base,
        "available": True,
        "analysis_status_counts": dict(sorted(analysis_counts.items())),
        "processing_status_counts": dict(sorted(processing_counts.items())),
        "processed_duration_seconds": round(float(processed[0] or 0), 3),
        "processed_frames": int(processed[1] or 0),
        "processed_bytes": int(processed[2] or 0),
        "sampled_states": int(analyzed[0] or 0),
        "raw_events": int(analyzed[1] or 0),
        "analyzed_ticks": int(analyzed[2] or 0),
    }


def build_snapshot(
    *,
    autonomy_path: Path = AUTONOMY_STATUS,
    current_path: Path = CURRENT_CHAMPION,
    live_path: Path = LIVE_CHAMPION,
    pipeline_path: Path = PIPELINE_SUMMARY,
    replay_db: Path = REPLAY_DB,
    allow_privileged_champion: bool = True,
) -> dict[str, Any]:
    autonomy, autonomy_error = _load_json(autonomy_path)
    current, current_error = _load_json(
        current_path,
        allow_privileged=allow_privileged_champion,
    )
    live, live_error = _load_json(
        live_path,
        allow_privileged=allow_privileged_champion,
    )
    pipeline, pipeline_error = _load_json(pipeline_path)

    autonomy_schema = _token(autonomy.get("schema"))
    autonomy_state = _token(autonomy.get("state"))
    autonomy_action = _token(autonomy.get("action"))
    autonomy_ok = (
        autonomy_error is None
        and autonomy_schema == "haxlab-autonomy-status-v2"
        and autonomy_state is not None
        and autonomy_action is not None
    )

    selection = pipeline.get("selection") if isinstance(pipeline.get("selection"), dict) else {}
    gate = pipeline.get("live_test_gate") if isinstance(pipeline.get("live_test_gate"), dict) else {}
    frozen = pipeline.get("frozen_holdout") if isinstance(pipeline.get("frozen_holdout"), dict) else {}
    validation = pipeline.get("validation") if isinstance(pipeline.get("validation"), dict) else {}

    live_health = live.get("live_health") if isinstance(live.get("live_health"), dict) else {}
    replay = _replay_snapshot(replay_db)

    pipeline_ok = pipeline_error is None and bool(pipeline)
    current_version = _token(current.get("version_id"))
    live_version = _token(live.get("version_id"))

    observed_candidates = [
        autonomy.get("updated_at") if autonomy_ok else None,
        _mtime_iso(autonomy_path),
        _mtime_iso(current_path),
        _mtime_iso(live_path),
        _mtime_iso(pipeline_path),
        replay.get("observed_at"),
    ]
    observed_at = max((str(x) for x in observed_candidates if x), default=None)

    return {
        "schema": "zcloud-haxlab-telemetry-v1",
        "available": bool(autonomy_ok or pipeline_ok or replay.get("available")),
        "observed_at": observed_at,
        "autonomy": {
            "available": autonomy_ok,
            "schema": autonomy_schema,
            "state": autonomy_state,
            "action": autonomy_action,
            "generation": int(autonomy["generation"]) if isinstance(autonomy.get("generation"), int) else None,
            "parent_champion": _token(autonomy.get("parent_champion")),
            "updated_at": _token(autonomy.get("updated_at"), 80),
            "source": str(autonomy_path),
            "error": autonomy_error,
        },
        "candidate": {
            "available": current_error is None and bool(current),
            "version_id": current_version,
            "source": str(current_path),
            "observed_at": _mtime_iso(current_path),
            "error": current_error,
        },
        "champion": {
            "available": live_error is None and bool(live),
            "version_id": live_version,
            "healthy": bool(live_health.get("healthy")) if live else None,
            "source": str(live_path),
            "observed_at": _mtime_iso(live_path),
            "error": live_error,
        },
        "evaluation": {
            "available": pipeline_ok and bool(gate),
            "gate": "offline_live_test_gate",
            "eligible": bool(gate.get("eligible_for_live_test")) if gate else None,
            "reason_count": len(gate.get("reasons") or []) if isinstance(gate.get("reasons"), list) else 0,
            "frozen_holdout_samples": int(frozen.get("samples") or 0) if frozen else None,
            "validation_samples": int(validation.get("samples") or 0) if validation else None,
            "source": str(pipeline_path),
            "observed_at": _mtime_iso(pipeline_path),
            "error": pipeline_error,
        },
        "training": {
            "available": pipeline_ok,
            "best_epoch": int(pipeline.get("best_epoch")) if isinstance(pipeline.get("best_epoch"), int) else None,
            "selected_replays": int(selection.get("selected_replays") or 0) if selection else None,
            "train_replays": int(selection.get("train_replay_count") or 0) if selection else None,
            "validation_replays": int(selection.get("validation_replay_count") or 0) if selection else None,
            "holdout_replays": int(selection.get("holdout_replay_count") or 0) if selection else None,
            "source": str(pipeline_path),
            "observed_at": _mtime_iso(pipeline_path),
            "error": pipeline_error,
        },
        "replay": replay,
        "meta": {
            "arena_v2_integrated": False,
            "arena_v2_note": (
                "Closed-Loop Arena v2 remains on the HaxLab experiment branch; "
                "do not infer a green Arena-v2 gate from the offline live-test gate."
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--require-core", action="store_true")
    args = parser.parse_args()

    snapshot = build_snapshot()
    if args.json:
        print(json.dumps(snapshot, sort_keys=True))
    else:
        print("ZCLOUD_HAXLAB_TELEMETRY=" + json.dumps(snapshot, sort_keys=True))

    if args.require_core:
        required = (
            snapshot["autonomy"]["available"],
            snapshot["candidate"]["available"],
            snapshot["champion"]["available"],
            snapshot["evaluation"]["available"],
            snapshot["training"]["available"],
            snapshot["replay"]["available"],
        )
        return 0 if all(required) else 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

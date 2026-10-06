#!/usr/bin/env python3
"""Project zCloud runner-live evidence into compact per-worker status lines."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

SCHEMA_VERSION = "zcloud-worker-status-lines-v1"
ALLOWED_STATES = {"paused", "draining", "starting", "stalled", "live", "stale", "offline"}


class StatusLineError(RuntimeError):
    pass


def _load(path: Path) -> object:
    if path.is_symlink():
        raise StatusLineError("symlink input refused")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StatusLineError("runner-live JSON unavailable or invalid") from exc


def _timestamp(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _text(value: object, *, max_len: int = 160) -> str | None:
    text = " ".join(str(value or "").split())
    if not text:
        return None
    return text[:max_len]


def _last_action(worker: dict) -> str | None:
    event = worker.get("last_event") if isinstance(worker.get("last_event"), dict) else {}
    command = worker.get("command") if isinstance(worker.get("command"), dict) else {}
    event_name = _text(event.get("event"), max_len=80)
    command_name = _text(command.get("action"), max_len=80)
    event_time = _timestamp(event.get("time"))
    command_time = _timestamp(command.get("updated_at") or command.get("created_at"))
    if command_name and (event_time is None or (command_time is not None and command_time >= event_time)):
        return "command:" + command_name
    return event_name


def build_lines(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise StatusLineError("runner-live payload must be an object")
    runners = payload.get("chatgpt_runners", payload)
    if not isinstance(runners, dict):
        raise StatusLineError("chatgpt_runners must be an object")

    lines = []
    malformed = 0
    for project_id in sorted(runners):
        project = runners.get(project_id)
        if not isinstance(project, dict):
            malformed += 1
            continue
        workers = project.get("workers")
        if not isinstance(workers, list):
            malformed += 1
            continue
        for worker in workers:
            if not isinstance(worker, dict):
                malformed += 1
                continue
            try:
                slot = int(worker.get("worker_slot"))
            except (TypeError, ValueError):
                malformed += 1
                continue
            if slot < 1:
                malformed += 1
                continue
            lane = _text(worker.get("work_area"), max_len=80)
            execution_lane = worker.get("execution_lane")
            if not lane and isinstance(execution_lane, dict):
                lane = _text(execution_lane.get("lane_id"), max_len=80)
            state = _text(worker.get("state"), max_len=32)
            if state not in ALLOWED_STATES:
                malformed += 1
                state = "offline"
            current_task = worker.get("current_task")
            task = None
            if isinstance(current_task, dict):
                task = _text(current_task.get("title") or current_task.get("claim_key"))
            lines.append(
                {
                    "project_id": _text(project_id, max_len=64),
                    "worker_slot": slot,
                    "lane": lane,
                    "task": task,
                    "status": state,
                    "last_action": _last_action(worker),
                }
            )

    return {
        "schema_version": SCHEMA_VERSION,
        "coverage_complete": malformed == 0,
        "malformed_workers": malformed,
        "worker_count": len(lines),
        "workers": lines,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runner-live", type=Path, required=True)
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = build_lines(_load(args.runner_live))
    except StatusLineError as exc:
        print(json.dumps({"schema_version": SCHEMA_VERSION, "ok": False, "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["coverage_complete"] or not args.require_complete else 2


if __name__ == "__main__":
    raise SystemExit(main())

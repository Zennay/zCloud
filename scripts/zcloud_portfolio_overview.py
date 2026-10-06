#!/usr/bin/env python3
"""Build a compact privacy-safe portfolio overview from existing zCloud read models."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

SCHEMA_VERSION = "zcloud-portfolio-overview-v1"
SEVERITY_ORDER = {"info": 0, "attention": 1, "urgent": 2}
QUEUE_STATES = ("queued", "running", "blocked", "failed", "paused", "draining")


class OverviewError(RuntimeError):
    pass


def _load(path: Path) -> object:
    if path.is_symlink():
        raise OverviewError(f"symlink input refused: {path.name}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OverviewError(f"invalid JSON input {path.name}") from exc


def _mapping(value: object, label: str) -> dict:
    if not isinstance(value, dict):
        raise OverviewError(f"{label} must be an object")
    return value


def _items(payload: object, label: str) -> list[dict]:
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict) and isinstance(payload.get("items"), list):
        rows = payload["items"]
    else:
        raise OverviewError(f"{label} items missing")
    if not all(isinstance(row, dict) for row in rows):
        raise OverviewError(f"{label} contains non-object items")
    return rows


def _project_id(value: object) -> str | None:
    text = str(value or "").strip().lower()
    if not text or len(text) > 64:
        return None
    if not all(ch.isalnum() or ch in "_-" for ch in text):
        return None
    return text


def build_overview(
    runner_live: object,
    queue: object,
    attention: object,
    resources: object,
) -> dict:
    runner_payload = _mapping(runner_live, "runner_live")
    runners = runner_payload.get("chatgpt_runners", runner_payload)
    runners = _mapping(runners, "chatgpt_runners")
    queue_rows = _items(queue, "queue")
    attention_rows = _items(attention, "attention")
    resource_rows = _mapping(resources, "resources")

    ids: set[str] = set()
    malformed = {"runner_live": 0, "queue": 0, "attention": 0, "resources": 0}

    for raw in runners:
        pid = _project_id(raw)
        if pid:
            ids.add(pid)
        else:
            malformed["runner_live"] += 1
    for row in queue_rows:
        pid = _project_id(row.get("project_id"))
        if pid:
            ids.add(pid)
        else:
            malformed["queue"] += 1
    for row in attention_rows:
        pid = _project_id(row.get("project_id"))
        if pid:
            ids.add(pid)
        else:
            malformed["attention"] += 1
    for raw in resource_rows:
        if raw == "_summary":
            continue
        pid = _project_id(raw)
        if pid:
            ids.add(pid)
        else:
            malformed["resources"] += 1

    projects = []
    for pid in sorted(ids):
        runner = runners.get(pid) if isinstance(runners.get(pid), dict) else {}
        try:
            active_workers = max(0, int(runner.get("active_worker_count") or 0))
            desired_workers = max(0, int(runner.get("desired_worker_count") or 0))
        except (TypeError, ValueError):
            malformed["runner_live"] += 1
            active_workers = desired_workers = 0

        q_counts = {state: 0 for state in QUEUE_STATES}
        other_queue = 0
        for row in queue_rows:
            if _project_id(row.get("project_id")) != pid:
                continue
            status = str(row.get("status") or "").strip().lower()
            if status in q_counts:
                q_counts[status] += 1
            elif status not in ("done", "dropped", "cancelled", ""):
                other_queue += 1

        open_attention = []
        for row in attention_rows:
            if _project_id(row.get("project_id")) != pid:
                continue
            if str(row.get("status") or "open").strip().lower() != "open":
                continue
            severity = str(row.get("severity") or "attention").strip().lower()
            if severity not in SEVERITY_ORDER:
                severity = "attention"
                malformed["attention"] += 1
            open_attention.append(severity)
        max_severity = (
            max(open_attention, key=lambda value: SEVERITY_ORDER[value])
            if open_attention
            else None
        )

        resource = resource_rows.get(pid)
        if resource is None:
            resource = {}
        elif not isinstance(resource, dict):
            malformed["resources"] += 1
            resource = {}
        cpu = resource.get("cpu_percent")
        memory = resource.get("memory_bytes")
        try:
            cpu = None if cpu is None else max(0.0, float(cpu))
            memory = None if memory is None else max(0, int(memory))
            active_units = max(0, int(resource.get("active_units") or 0))
        except (TypeError, ValueError):
            malformed["resources"] += 1
            cpu = memory = None
            active_units = 0

        uses_resources = bool(
            active_units > 0
            or (cpu is not None and cpu > 0)
            or (memory is not None and memory > 0)
        )
        blocked = q_counts["blocked"] > 0
        needs_attention = blocked or bool(open_attention) or q_counts["failed"] > 0
        projects.append(
            {
                "project_id": pid,
                "running": active_workers > 0,
                "active_workers": active_workers,
                "desired_workers": desired_workers,
                "uses_resources": uses_resources,
                "resource": {
                    "managed": bool(resource.get("managed")),
                    "active_units": active_units,
                    "cpu_percent": cpu,
                    "memory_bytes": memory,
                    "priority": str(resource.get("priority") or "") or None,
                },
                "queue": {**q_counts, "other": other_queue},
                "blocked": blocked,
                "attention_count": len(open_attention),
                "attention_severity": max_severity,
                "needs_attention": needs_attention,
            }
        )

    malformed_total = sum(malformed.values())
    return {
        "schema_version": SCHEMA_VERSION,
        "coverage_complete": malformed_total == 0,
        "malformed": malformed,
        "summary": {
            "projects": len(projects),
            "running": sum(item["running"] for item in projects),
            "using_resources": sum(item["uses_resources"] for item in projects),
            "blocked": sum(item["blocked"] for item in projects),
            "needs_attention": sum(item["needs_attention"] for item in projects),
        },
        "projects": projects,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runner-live", type=Path, required=True)
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--attention", type=Path, required=True)
    parser.add_argument("--resources", type=Path, required=True)
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = build_overview(
            _load(args.runner_live),
            _load(args.queue),
            _load(args.attention),
            _load(args.resources),
        )
    except OverviewError as exc:
        print(json.dumps({"schema_version": SCHEMA_VERSION, "ok": False, "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["coverage_complete"] or not args.require_complete else 2


if __name__ == "__main__":
    raise SystemExit(main())

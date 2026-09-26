#!/usr/bin/env python3
"""Read-only worker scaling report from zCloud telemetry.

This is observational telemetry, not a causal benchmark. It intentionally
reports insufficient_data when the evidence window is too thin.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

BLOCK_EVENTS = {
    "startup-blocked",
    "send-blocked",
    "stall-detected",
    "composer-stalled",
    "auto-continue-blocked",
    "push-skipped",
}
UNBLOCK_EVENTS = {
    "prompt-sent",
    "generation-started",
    "generation-finished",
    "injection-success",
    "conversation-adopted",
}
MAX_HEARTBEAT_GAP_SECONDS = 150
MIN_EXTRA_OBSERVED_SECONDS = 30 * 60
MIN_EXTRA_COMPLETIONS = 3


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


def _pct(seconds: float, total: float) -> float:
    return round((seconds / total * 100.0) if total > 0 else 0.0, 1)


def _open_ro(db: Path) -> sqlite3.Connection:
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def _has_table(c: sqlite3.Connection, name: str) -> bool:
    return bool(c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def worker_metrics(rows: list[sqlite3.Row]) -> dict:
    if not rows:
        return {
            "observed_seconds": 0,
            "working_seconds": 0,
            "idle_seconds": 0,
            "blocked_seconds": 0,
            "working_pct": 0.0,
            "idle_pct": 0.0,
            "blocked_pct": 0.0,
            "completed_generations": 0,
            "throughput_per_observed_hour": None,
            "blocked_events": 0,
            "first_event": None,
            "last_event": None,
        }

    ordered = sorted(rows, key=lambda r: r["ts"])
    completed = sum(1 for r in ordered if r["event"] == "generation-finished")
    blocked_events = sum(1 for r in ordered if r["event"] in BLOCK_EVENTS)
    first_event, last_event = ordered[0]["ts"], ordered[-1]["ts"]

    blocked_state = False
    heartbeats = []
    for row in ordered:
        event = row["event"]
        if event in BLOCK_EVENTS:
            blocked_state = True
        elif event in UNBLOCK_EVENTS:
            blocked_state = False
        if event == "heartbeat":
            heartbeats.append((row, blocked_state))

    working = idle = blocked = observed = 0.0
    for (left, left_blocked), (right, _right_blocked) in zip(heartbeats, heartbeats[1:]):
        delta = (_dt(right["ts"]) - _dt(left["ts"])).total_seconds()
        if delta <= 0 or delta > MAX_HEARTBEAT_GAP_SECONDS:
            continue
        observed += delta
        if bool(left["generating"]):
            working += delta
        elif left_blocked:
            blocked += delta
        else:
            idle += delta

    throughput = round(completed / (observed / 3600.0), 2) if observed >= 60 and completed else (0.0 if observed >= 60 else None)
    return {
        "observed_seconds": round(observed),
        "working_seconds": round(working),
        "idle_seconds": round(idle),
        "blocked_seconds": round(blocked),
        "working_pct": _pct(working, observed),
        "idle_pct": _pct(idle, observed),
        "blocked_pct": _pct(blocked, observed),
        "completed_generations": completed,
        "throughput_per_observed_hour": throughput,
        "blocked_events": blocked_events,
        "first_event": first_event,
        "last_event": last_event,
    }


def scaling_assessment(workers: list[dict], desired_workers: int) -> dict:
    considered = [w for w in workers if int(w["worker_slot"]) <= max(1, int(desired_workers or 1))]
    if desired_workers <= 1 or len(considered) <= 1:
        return {"state": "single_worker", "reason": "Er is geen actuele multi-worker vergelijking nodig."}

    primary = next((w for w in considered if int(w["worker_slot"]) == 1), None)
    extras = [w for w in considered if int(w["worker_slot"]) > 1]
    if not primary or primary["metrics"]["throughput_per_observed_hour"] is None:
        return {"state": "insufficient_data", "reason": "De primaire worker heeft nog te weinig betrouwbare observatietijd."}

    eligible = [
        w for w in extras
        if w["metrics"]["observed_seconds"] >= MIN_EXTRA_OBSERVED_SECONDS
        and w["metrics"]["completed_generations"] >= MIN_EXTRA_COMPLETIONS
        and w["metrics"]["throughput_per_observed_hour"] is not None
    ]
    if len(eligible) != len(extras):
        return {
            "state": "insufficient_data",
            "reason": "Minstens één extra worker heeft minder dan 30 minuten of 3 afgeronde generaties betrouwbare data.",
        }

    primary_rate = max(float(primary["metrics"]["throughput_per_observed_hour"] or 0), 0.01)
    ratios = [float(w["metrics"]["throughput_per_observed_hour"]) / primary_rate for w in eligible]
    nonwork = [
        (float(w["metrics"]["idle_pct"]) + float(w["metrics"]["blocked_pct"])) / 100.0
        for w in eligible
    ]
    avg_ratio = sum(ratios) / len(ratios)
    avg_nonwork = sum(nonwork) / len(nonwork)

    if avg_ratio < 0.35 and avg_nonwork >= 0.60:
        state = "diminishing_returns"
        reason = "Extra workers leveren weinig afgeronde runs per geobserveerd uur en zijn meestal idle/geblokkeerd."
    elif avg_ratio >= 0.60:
        state = "useful_scaling"
        reason = "Extra workers leveren in deze observatie nog substantieel eigen throughput."
    else:
        state = "inconclusive"
        reason = "De extra throughput is gemengd; meer vergelijkbare observatietijd is nodig."
    return {
        "state": state,
        "reason": reason,
        "extra_vs_primary_throughput_ratio": round(avg_ratio, 2),
        "extra_idle_blocked_pct": round(avg_nonwork * 100.0, 1),
    }


def report(db: Path, hours: float = 12.0, project: str | None = None, now: datetime | None = None) -> dict:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cutoff = now - timedelta(hours=max(0.25, float(hours)))
    with closing(_open_ro(db)) as c:
        params: list[object] = [cutoff.isoformat()]
        where = "ts>=? AND project_id IS NOT NULL AND project_id<>''"
        if project:
            where += " AND project_id=?"
            params.append(project)
        rows = c.execute(
            f"SELECT ts,event,generating,sending,reason,error,project_id,worker_slot FROM runner_events WHERE {where} ORDER BY ts",
            params,
        ).fetchall()

        desired = {}
        if _has_table(c, "runner_targets"):
            for row in c.execute("SELECT project_id,worker_count FROM runner_targets"):
                desired[str(row["project_id"])] = max(1, int(row["worker_count"] or 1))

        conflicts = defaultdict(int)
        if _has_table(c, "alerts"):
            for row in c.execute("SELECT project,COUNT(*) n FROM alerts WHERE kind='claim_conflict' AND ts>=? GROUP BY project", (cutoff.isoformat(),)):
                conflicts[str(row["project"] or "cloud")] = int(row["n"])

    grouped: dict[tuple[str, int], list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        grouped[(str(row["project_id"]), int(row["worker_slot"] or 1))].append(row)

    by_project: dict[str, list[dict]] = defaultdict(list)
    for (pid, slot), events in grouped.items():
        by_project[pid].append({"worker_slot": slot, "metrics": worker_metrics(events)})

    projects = []
    for pid, workers in sorted(by_project.items()):
        workers.sort(key=lambda x: x["worker_slot"])
        desired_count = desired.get(pid, max((w["worker_slot"] for w in workers), default=1))
        total_finished = sum(w["metrics"]["completed_generations"] for w in workers if w["worker_slot"] <= desired_count)
        total_worker_hours = sum(w["metrics"]["observed_seconds"] for w in workers if w["worker_slot"] <= desired_count) / 3600.0
        projects.append({
            "project_id": pid,
            "desired_workers": desired_count,
            "observed_worker_slots": [w["worker_slot"] for w in workers],
            "completed_generations": total_finished,
            "completed_per_worker_hour": round(total_finished / total_worker_hours, 2) if total_worker_hours > 0 else None,
            "duplicate_work_prevented": conflicts.get(pid, 0),
            "assessment": scaling_assessment(workers, desired_count),
            "workers": workers,
        })

    return {
        "generated_at": now.isoformat(),
        "window_hours": float(hours),
        "cutoff": cutoff.isoformat(),
        "method": "observational runner telemetry; heartbeat gaps >150s excluded; no causal claim",
        "projects": projects,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure zCloud worker scaling from read-only telemetry")
    parser.add_argument("--db", type=Path, default=Path(__file__).resolve().parents[1] / "history.db")
    parser.add_argument("--hours", type=float, default=12.0)
    parser.add_argument("--project")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    payload = report(args.db, args.hours, args.project)
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

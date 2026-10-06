#!/usr/bin/env python3
"""Bounded live continuity debugger/remediator for zCloud workers.

This tool intentionally stays outside server.py and the production watchdog. It
collects correlated API/SQLite/host evidence and only remediates through existing
zCloud control APIs. It never writes history.db directly.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_DB = Path("/home/ubuntu/zennay-cloud/history.db")
DEFAULT_BASE_URL = "http://127.0.0.1:8765"
DYNAMIC_KEYS = (
    "chatgpt_count",
    "claude_count",
    "chatgpt_cooldown_seconds",
    "claude_cooldown_seconds",
    "check_interval_ms",
    "tick_interval_ms",
    "heartbeat_interval_ms",
    "generation_start_timeout_ms",
    "scheduler_interval_seconds",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def api_call(base_url: str, method: str, path: str, payload: dict[str, Any] | None = None, timeout: float = 8.0):
    data = None
    headers = {"User-Agent": "zcloud-worker-continuity-debug/1"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base_url.rstrip("/") + path, data=data, headers=headers, method=method)
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = json.load(response)
            return {
                "status": int(response.status),
                "ok": int(response.status) < 300,
                "elapsed_ms": round((time.monotonic() - started) * 1000),
                "body": body,
            }
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            body = json.loads(raw)
        except Exception:
            body = {"error": raw[:1000]}
        return {
            "status": int(exc.code),
            "ok": False,
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "body": body,
        }
    except Exception as exc:
        return {
            "status": 0,
            "ok": False,
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "body": {"error": f"{type(exc).__name__}: {exc}"},
        }


def meminfo() -> dict[str, Any]:
    values: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, rest = line.split(":", 1)
            values[key] = int(rest.strip().split()[0])
    except Exception:
        return {}
    total = values.get("MemTotal", 0)
    available = values.get("MemAvailable", 0)
    return {
        "total_kib": total,
        "available_kib": available,
        "available_pct": round((available / total) * 100, 2) if total else None,
        "swap_free_kib": values.get("SwapFree"),
        "swap_total_kib": values.get("SwapTotal"),
    }


def host_load() -> dict[str, Any]:
    try:
        one, five, fifteen = os.getloadavg()
    except OSError:
        one = five = fifteen = 0.0
    cores = os.cpu_count() or 1
    return {
        "cores": cores,
        "load1": round(one, 2),
        "load5": round(five, 2),
        "load15": round(fifteen, 2),
        "load1_per_core": round(one / cores, 2),
    }


def firefox_processes() -> dict[str, Any]:
    proc = subprocess.run(
        ["pgrep", "-u", str(os.getuid()), "-f", r"/snap/firefox/.*/usr/lib/firefox/firefox"],
        text=True,
        capture_output=True,
        check=False,
    )
    pids = [int(item) for item in proc.stdout.split() if item.isdigit()]
    nice_values: list[int] = []
    for pid in pids[:20]:
        try:
            raw = Path(f"/proc/{pid}/stat").read_text()
            # proc(5): nice is field 19; comm may contain spaces inside (...), so
            # split only after the closing parenthesis.
            rest = raw.rsplit(")", 1)[1].strip().split()
            nice_values.append(int(rest[16]))
        except Exception:
            continue
    return {
        "count": len(pids),
        "pids": pids[:20],
        "nice_values": nice_values,
        "min_nice": min(nice_values) if nice_values else None,
        "max_nice": max(nice_values) if nice_values else None,
    }


def firefox_systemd_policy() -> dict[str, Any]:
    env = os.environ.copy()
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={env['XDG_RUNTIME_DIR']}/bus")
    proc = subprocess.run(
        [
            "systemctl", "--user", "show", "chatgpt-firefox.service",
            "-p", "LoadState",
            "-p", "ActiveState",
            "-p", "SubState",
            "-p", "Nice",
            "-p", "CPUWeight",
            "-p", "CPUQuotaPerSecUSec",
        ],
        text=True,
        capture_output=True,
        check=False,
        env=env,
        timeout=5,
    )
    fields: dict[str, Any] = {}
    for line in proc.stdout.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        fields[key] = value
    try:
        fields["Nice"] = int(fields.get("Nice") or 0)
    except Exception:
        pass
    try:
        fields["CPUWeight"] = int(fields.get("CPUWeight") or 0)
    except Exception:
        pass
    return {
        "ok": proc.returncode == 0,
        "fields": fields,
        "error": proc.stderr.strip()[:500] if proc.returncode else "",
    }


def sqlite_snapshot(db_path: Path, *, quick_check: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {"ok": False}
    try:
        with sqlite3.connect(db_path, timeout=3) as conn:
            conn.row_factory = sqlite3.Row
            quick = conn.execute("PRAGMA quick_check").fetchone()[0] if quick_check else "skipped"
            slots = [
                dict(row)
                for row in conn.execute(
                    "SELECT slot,project_id,worker_slot,assigned_at FROM ai_global_slots ORDER BY slot"
                ).fetchall()
            ]
            workers = [
                dict(row)
                for row in conn.execute(
                    "SELECT project_id,worker_slot,provider,desired_state,conversation_id "
                    "FROM runner_workers ORDER BY project_id,worker_slot"
                ).fetchall()
            ]
            pending = [
                dict(row)
                for row in conn.execute(
                    "SELECT id,project_id,action,status,created_at FROM runner_commands "
                    "WHERE status='pending' ORDER BY id DESC LIMIT 20"
                ).fetchall()
            ]
        result.update({"ok": quick in {"ok", "skipped"}, "quick_check": quick, "slots": slots, "workers": workers, "pending": pending})
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def worker_key(item: dict[str, Any]) -> str:
    project = str(item.get("project_id") or item.get("base_project_id") or "").strip()
    if "::w" in project:
        return project
    try:
        slot = max(1, int(item.get("worker_slot") or 1))
    except Exception:
        slot = 1
    return f"{project}::w{slot}" if project else ""


def extract_runtime(payload: dict[str, Any], *, ghost_after_seconds: int = 600) -> tuple[set[str], set[str], set[str], dict[str, dict[str, Any]]]:
    live: set[str] = set()
    busy: set[str] = set()
    ghost_generating: set[str] = set()
    details: dict[str, dict[str, Any]] = {}
    candidates: list[dict[str, Any]] = []

    # /api/status and /api/runner-live expose workers inside
    # chatgpt_runners[base_project].workers. Keep the older generic shapes too so
    # the debugger remains useful against older deployments.
    runners = payload.get("chatgpt_runners")
    if isinstance(runners, dict):
        for project in runners.values():
            if isinstance(project, dict) and isinstance(project.get("workers"), list):
                candidates.extend(item for item in project["workers"] if isinstance(item, dict))
    if isinstance(payload.get("workers"), list):
        candidates.extend(item for item in payload["workers"] if isinstance(item, dict))
    projects = payload.get("projects")
    if isinstance(projects, list):
        for project in projects:
            if isinstance(project, dict) and isinstance(project.get("workers"), list):
                candidates.extend(item for item in project["workers"] if isinstance(item, dict))

    for item in candidates:
        key = str(item.get("worker_id") or worker_key(item)).strip()
        if not key:
            continue
        state = str(item.get("state") or "").lower()
        generating = bool(item.get("generating"))
        sending = bool(item.get("sending"))
        try:
            progress_age = int(item.get("progress_age_seconds")) if item.get("progress_age_seconds") is not None else None
        except Exception:
            progress_age = None
        try:
            event_age = int(item.get("age_seconds")) if item.get("age_seconds") is not None else None
        except Exception:
            event_age = None

        details[key] = {
            "state": state,
            "generating": generating,
            "sending": sending,
            "progress_age_seconds": progress_age,
            "event_age_seconds": event_age,
            "last_event": item.get("last_event"),
            "conversation_id": item.get("conversation_id"),
        }
        if state not in {"offline", "missing", "stale", "dead"}:
            live.add(key)
        if generating or sending:
            busy.add(key)
        # A live heartbeat with a generation flag but no output progress for ten
        # minutes is exactly the "stuck generating" false-liveness case this
        # session is meant to catch. Do not infer it from one stale API read.
        if generating and progress_age is not None and progress_age >= ghost_after_seconds and (event_age is None or event_age <= 120):
            ghost_generating.add(key)

    return live, busy, ghost_generating, details


def desired_counts(dynamic: dict[str, Any]) -> tuple[int, int, int]:
    chatgpt = max(0, int(dynamic.get("chatgpt_count") or 0))
    claude = max(0, int(dynamic.get("claude_count") or 0))
    return chatgpt, claude, chatgpt + claude


def classify(snapshot: dict[str, Any], min_workers: int) -> list[str]:
    reasons: list[str] = []
    desired = snapshot["desired_total"]
    allocated = snapshot["allocated_count"]
    live = snapshot["live_count"]
    api_ok = snapshot["api_ok"]
    mem_pct = snapshot["memory"].get("available_pct")
    load_ratio = snapshot["load"].get("load1_per_core", 0)

    if desired < min_workers:
        reasons.append("desired-pool-below-requested-floor")
    if allocated < desired:
        reasons.append("allocator-capacity-loss")
    if live < allocated:
        reasons.append("browser-runtime-gap")
    if not api_ok:
        reasons.append("control-api-degraded")
    if mem_pct is not None and mem_pct < 8:
        reasons.append("memory-pressure")
    if load_ratio >= 1.5:
        reasons.append("cpu-load-pressure")
    if snapshot["firefox"]["count"] == 0:
        reasons.append("firefox-process-missing")
    policy = (snapshot.get("firefox_systemd") or {}).get("fields") or {}
    nice = policy.get("Nice")
    if isinstance(nice, int) and nice > 0 and desired >= 4:
        reasons.append("browser-deprioritized")
    if snapshot.get("ghost_generating_keys"):
        reasons.append("ghost-generating")
    if not reasons:
        reasons.append("healthy")
    return reasons


def sample(base_url: str, db_path: Path, min_workers: int, *, heavy: bool = False) -> dict[str, Any]:
    dynamic = api_call(base_url, "GET", "/api/dynamic-workers")
    targets = api_call(base_url, "GET", "/api/runner-targets")
    runner_live = api_call(base_url, "GET", "/api/runner-live")
    # /api/status is the expensive aggregate read-model. Polling it every few
    # seconds can make the debugger itself part of the incident under CPU/DB
    # pressure. Use the narrow runner-live path continuously and take a full
    # status sample only periodically or as a fallback when runner-live fails.
    status = (
        api_call(base_url, "GET", "/api/status")
        if heavy or not runner_live["ok"]
        else {"status": 0, "ok": False, "elapsed_ms": 0, "body": {"skipped": True}}
    )

    settings = (dynamic["body"].get("dynamic_workers") or {}) if dynamic["ok"] else {}
    chatgpt, claude, desired = desired_counts(settings)

    allocation = []
    if targets["ok"]:
        allocation = ((targets["body"].get("global_allocation") or {}).get("workers") or [])
    allocated_keys = {worker_key(item) for item in allocation if isinstance(item, dict) and worker_key(item)}

    runtime_payload = runner_live["body"] if runner_live["ok"] else status["body"]
    live_keys, busy_keys, ghost_generating_keys, runtime_details = extract_runtime(runtime_payload)

    snap = {
        "ts": utc_now(),
        "dynamic": dynamic,
        "targets": targets,
        "status": status,
        "runner_live": runner_live,
        "sqlite": sqlite_snapshot(db_path, quick_check=heavy),
        "memory": meminfo(),
        "load": host_load(),
        "firefox": firefox_processes(),
        "firefox_systemd": firefox_systemd_policy() if heavy else {"ok": True, "fields": {}, "skipped": True},
        "desired_chatgpt": chatgpt,
        "desired_claude": claude,
        "desired_total": desired,
        "allocated_count": len(allocated_keys),
        "allocated_keys": sorted(allocated_keys),
        "live_count": len(live_keys & allocated_keys),
        "live_keys": sorted(live_keys & allocated_keys),
        "missing_live_keys": sorted(allocated_keys - live_keys),
        "busy_keys": sorted(busy_keys & allocated_keys),
        "ghost_generating_keys": sorted(ghost_generating_keys & allocated_keys),
        "runtime_details": {key: runtime_details[key] for key in sorted(allocated_keys & set(runtime_details))},
        "api_ok": bool(dynamic["ok"] and targets["ok"] and (runner_live["ok"] or status["ok"])),
    }
    snap["diagnosis"] = classify(snap, min_workers)
    return snap


def compact_sample(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Keep long-run evidence useful without retaining multi-megabyte API bodies."""
    return {
        "ts": snapshot.get("ts"),
        "desired_total": snapshot.get("desired_total"),
        "desired_chatgpt": snapshot.get("desired_chatgpt"),
        "desired_claude": snapshot.get("desired_claude"),
        "allocated_count": snapshot.get("allocated_count"),
        "allocated_keys": snapshot.get("allocated_keys"),
        "live_count": snapshot.get("live_count"),
        "live_keys": snapshot.get("live_keys"),
        "missing_live_keys": snapshot.get("missing_live_keys"),
        "busy_keys": snapshot.get("busy_keys"),
        "ghost_generating_keys": snapshot.get("ghost_generating_keys"),
        "runtime_details": snapshot.get("runtime_details"),
        "diagnosis": snapshot.get("diagnosis"),
        "memory": snapshot.get("memory"),
        "load": snapshot.get("load"),
        "firefox": snapshot.get("firefox"),
        "firefox_systemd": snapshot.get("firefox_systemd"),
        "api_latency_ms": {
            "dynamic": (snapshot.get("dynamic") or {}).get("elapsed_ms"),
            "targets": (snapshot.get("targets") or {}).get("elapsed_ms"),
            "runner_live": (snapshot.get("runner_live") or {}).get("elapsed_ms"),
            "status": (snapshot.get("status") or {}).get("elapsed_ms"),
        },
        "sqlite": {
            "ok": (snapshot.get("sqlite") or {}).get("ok"),
            "quick_check": (snapshot.get("sqlite") or {}).get("quick_check"),
            "slot_count": len((snapshot.get("sqlite") or {}).get("slots") or []),
            "pending_count": len((snapshot.get("sqlite") or {}).get("pending") or []),
        },
        "remediation": snapshot.get("remediation") or [],
    }


def reconcile(base_url: str, dynamic_body: dict[str, Any]) -> dict[str, Any]:
    settings = dynamic_body.get("dynamic_workers") or {}
    payload = {key: settings[key] for key in DYNAMIC_KEYS if key in settings}
    if not payload:
        return {"action": "reconcile", "ok": False, "reason": "no-dynamic-settings"}
    response = api_call(base_url, "POST", "/api/dynamic-workers", payload, timeout=12)
    return {"action": "reconcile", **response}


def runner_action(base_url: str, project_id: str, action: str) -> dict[str, Any]:
    response = api_call(
        base_url,
        "POST",
        "/api/runner-control",
        {"project_id": project_id, "action": action},
        timeout=12,
    )
    return {"action": action, "project_id": project_id, **response}


def action_due(last_actions: dict[str, float], key: str, now: float, cooldown: float) -> bool:
    previous = last_actions.get(key)
    return previous is None or now - previous >= cooldown


def maybe_remediate(
    snapshot: dict[str, Any],
    *,
    base_url: str,
    streaks: dict[str, int],
    worker_streaks: dict[str, int],
    last_actions: dict[str, float],
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    now = time.monotonic()

    underfilled = snapshot["allocated_count"] < snapshot["desired_total"]
    streaks["underfilled"] = streaks.get("underfilled", 0) + 1 if underfilled else 0

    # Re-submit the already persisted settings after two consecutive underfilled
    # samples. This asks the existing allocator to reconcile without inventing or
    # writing allocation state in this debugger.
    if streaks["underfilled"] >= 2 and action_due(last_actions, "reconcile", now, 60):
        actions.append(reconcile(base_url, snapshot["dynamic"]["body"]))
        last_actions["reconcile"] = now

    missing = set(snapshot["missing_live_keys"])
    busy = set(snapshot["busy_keys"])
    ghost_generating = set(snapshot.get("ghost_generating_keys") or [])
    for key in snapshot["allocated_keys"]:
        worker_streaks[key] = worker_streaks.get(key, 0) + 1 if key in missing else 0

    # A persistently allocated-but-missing worker gets a fresh conversation only
    # after three samples, and never while the runtime says it is generating.
    for key in sorted(missing - busy):
        if worker_streaks.get(key, 0) >= 3 and action_due(last_actions, f"new_chat:{key}", now, 180):
            actions.append(runner_action(base_url, key, "new_chat"))
            last_actions[f"new_chat:{key}"] = now

    # A worker that has kept "generating" true while progress stays unchanged for
    # ten minutes gets a per-worker new-chat request first. The userscript may
    # legitimately defer it if ChatGPT is truly still generating; the evidence
    # then tells us whether the DOM liveness detector itself is the problem.
    for key in sorted(ghost_generating):
        ghost_key = f"ghost:{key}"
        streaks[ghost_key] = streaks.get(ghost_key, 0) + 1
        if streaks[ghost_key] >= 2 and action_due(last_actions, f"new_chat:{key}", now, 300):
            actions.append(runner_action(base_url, key, "new_chat"))
            last_actions[f"new_chat:{key}"] = now

    # If a ghost-generating worker could not consume the per-worker new_chat
    # request, recycle Firefox only after a further grace window and only when
    # every *other* allocated worker is idle. This recreates the simple 1-Oct
    # standalone-browser recovery without interrupting useful parallel work.
    ghost_restart_ready = False
    other_busy = busy - ghost_generating
    if ghost_generating and not other_busy:
        for key in sorted(ghost_generating):
            requested_at = last_actions.get(f"new_chat:{key}")
            if requested_at is not None and now - requested_at >= 120:
                ghost_restart_ready = True
                break

    mem_pct = snapshot["memory"].get("available_pct")
    firefox_missing = snapshot["firefox"]["count"] == 0
    streaks["firefox_missing"] = streaks.get("firefox_missing", 0) + 1 if firefox_missing else 0
    all_runtime_missing = bool(snapshot["allocated_keys"]) and snapshot["live_count"] == 0
    pressure = (mem_pct is not None and mem_pct < 8) or snapshot["load"].get("load1_per_core", 0) >= 2.0

    # Firefox recycle is deliberately a last resort: process missing, or the
    # whole allocated runtime disappeared while the host is under severe pressure.
    if ((streaks["firefox_missing"] >= 2) or (all_runtime_missing and pressure) or ghost_restart_ready) and action_due(last_actions, "restart_firefox", now, 300):
        actions.append(runner_action(base_url, "", "restart_firefox"))
        last_actions["restart_firefox"] = now

    return actions


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Observe and safely recover zCloud worker continuity")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--duration-seconds", type=int, default=180)
    parser.add_argument("--interval-seconds", type=int, default=10)
    parser.add_argument("--min-workers", type=int, default=8)
    parser.add_argument("--remediate", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    deadline = time.monotonic() + max(1, args.duration_seconds)
    started_at = utc_now()
    streaks: dict[str, int] = {}
    worker_streaks: dict[str, int] = {}
    last_actions: dict[str, float] = {}
    history: list[dict[str, Any]] = []
    diagnoses_seen: set[str] = set()
    action_log: list[dict[str, Any]] = []
    sample_count = 0

    while True:
        sample_count += 1
        # One heavier integrity/status sample at startup and then every ~5 min
        # with the production 15s cadence. Everything else uses narrow reads.
        heavy = sample_count == 1 or sample_count % 20 == 0
        snap = sample(args.base_url, args.db, args.min_workers, heavy=heavy)
        actions: list[dict[str, Any]] = []
        if args.remediate:
            actions = maybe_remediate(
                snap,
                base_url=args.base_url,
                streaks=streaks,
                worker_streaks=worker_streaks,
                last_actions=last_actions,
            )
        snap["remediation"] = actions
        compact = compact_sample(snap)
        diagnoses_seen.update(compact["diagnosis"] or [])
        action_log.extend(actions)
        history.append(compact)
        # Bounded in-memory/evidence tail: enough for ~1h at 15s cadence, while
        # aggregate diagnoses/actions cover the complete run.
        if len(history) > 240:
            history = history[-240:]

        print(json.dumps(compact, ensure_ascii=False, sort_keys=True), flush=True)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            live_summary = {
                "ok": True,
                "running": True,
                "started_at": started_at,
                "last_sample_at": compact["ts"],
                "samples": sample_count,
                "min_workers": args.min_workers,
                "remediate": args.remediate,
                "latest": compact,
                "diagnoses": sorted(diagnoses_seen),
                "actions": action_log[-100:],
            }
            temp = args.output.with_suffix(args.output.suffix + ".tmp")
            temp.write_text(json.dumps(live_summary, indent=2, ensure_ascii=False))
            temp.replace(args.output)

        if time.monotonic() >= deadline:
            break
        time.sleep(max(1, args.interval_seconds))

    summary = {
        "ok": True,
        "started_at": started_at,
        "finished_at": utc_now(),
        "samples": sample_count,
        "retained_samples": len(history),
        "min_workers": args.min_workers,
        "remediate": args.remediate,
        "desired_counts_tail": [item["desired_total"] for item in history],
        "allocated_counts_tail": [item["allocated_count"] for item in history],
        "live_counts_tail": [item["live_count"] for item in history],
        "diagnoses": sorted(diagnoses_seen),
        "actions": action_log,
    }
    print("ZCLOUD_CONTINUITY_SUMMARY=" + json.dumps(summary, ensure_ascii=False, sort_keys=True), flush=True)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        final_payload = {"running": False, "summary": summary, "samples": history}
        temp = args.output.with_suffix(args.output.suffix + ".tmp")
        temp.write_text(json.dumps(final_payload, indent=2, ensure_ascii=False))
        temp.replace(args.output)

    # Diagnostic failures should be visible without making transient pressure a
    # workflow failure. Fail only when both desired allocation and live runtime
    # remain below the requested floor for the entire final third of the run.
    tail = history[max(0, len(history) * 2 // 3):]
    hard_gap = bool(tail) and all(
        item["allocated_count"] < min(item["desired_total"], args.min_workers)
        or item["live_count"] < min(item["allocated_count"], args.min_workers)
        for item in tail
    )
    return 3 if hard_gap else 0


if __name__ == "__main__":
    raise SystemExit(main())

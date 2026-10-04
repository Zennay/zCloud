#!/usr/bin/env python3
"""Material-progress watchdog for zCloud dynamic workers.

Runs once per invocation (normally from zcloud-self-heal.timer every minute).
It distinguishes browser liveness from useful progress:
- material progress: queue result/evidence/status changes or sampled git commit/progress changes;
- activity: generation/prompt/browser events, used only as a grace signal.

Recovery is intentionally staged: push -> worker new_chat -> (only when every allocated
worker is persistently stuck after repeated restarts) Firefox host restart.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_ROOT = Path(os.environ.get("ZCLOUD_ROOT", "/home/ubuntu/zennay-cloud"))
DEFAULT_DB = Path(os.environ.get("ZCLOUD_DB", str(DEFAULT_ROOT / "history.db")))
DEFAULT_BASE_URL = os.environ.get("ZCLOUD_BASE_URL", "http://127.0.0.1:8765")
DEFAULT_STATE = Path(os.environ.get(
    "ZCLOUD_WORKER_WATCHDOG_STATE",
    "/var/lib/zcloud/worker-progress-watchdog.json",
))

PUSH_AFTER_SECONDS = int(os.environ.get("ZCLOUD_WATCHDOG_PUSH_AFTER_SECONDS", "180"))
RESTART_AFTER_SECONDS = int(os.environ.get("ZCLOUD_WATCHDOG_RESTART_AFTER_SECONDS", "420"))
OFFLINE_RESTART_AFTER_SECONDS = int(os.environ.get("ZCLOUD_WATCHDOG_OFFLINE_RESTART_AFTER_SECONDS", "120"))
QUIET_GRACE_SECONDS = int(os.environ.get("ZCLOUD_WATCHDOG_QUIET_GRACE_SECONDS", "120"))
ACTION_COOLDOWN_SECONDS = int(os.environ.get("ZCLOUD_WATCHDOG_ACTION_COOLDOWN_SECONDS", "120"))
NEW_CHAT_COOLDOWN_SECONDS = int(os.environ.get("ZCLOUD_WATCHDOG_NEW_CHAT_COOLDOWN_SECONDS", "300"))
GLOBAL_RECOVERY_AFTER_SECONDS = int(os.environ.get("ZCLOUD_WATCHDOG_GLOBAL_RECOVERY_AFTER_SECONDS", "900"))
GLOBAL_RECOVERY_COOLDOWN_SECONDS = int(os.environ.get("ZCLOUD_WATCHDOG_GLOBAL_RECOVERY_COOLDOWN_SECONDS", "900"))
FIREFOX_RUNTIME_RESTART_COOLDOWN_SECONDS = int(os.environ.get("ZCLOUD_WATCHDOG_FIREFOX_RUNTIME_RESTART_COOLDOWN_SECONDS", "120"))
GENERATION_PROTECT_SECONDS = int(os.environ.get("ZCLOUD_WATCHDOG_GENERATION_PROTECT_SECONDS", "1200"))
PROMPT_STALE_REFRESH_SECONDS = int(os.environ.get("ZCLOUD_WATCHDOG_PROMPT_STALE_REFRESH_SECONDS", "300"))
STALE_PENDING_COMMAND_SECONDS = int(os.environ.get("ZCLOUD_WATCHDOG_STALE_PENDING_COMMAND_SECONDS", "300"))

HUMAN_GATE_STATUSES = {"wait_human", "waiting_human", "human_gate", "needs_human", "approval_required"}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def age_seconds(value: Any, now: datetime) -> int | None:
    ts = parse_time(value)
    if ts is None:
        return None
    return max(0, int((now - ts).total_seconds()))


def worker_key(item: dict[str, Any]) -> str:
    key = str(item.get("worker_key") or "").strip()
    if key:
        return key
    project = str(item.get("project_id") or "").strip()
    try:
        slot = max(1, int(item.get("worker_slot") or 1))
    except Exception:
        slot = 1
    return f"{project}::w{slot}" if project else ""


def api_call(base_url: str, method: str, path: str, payload: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    data = None
    headers = {"User-Agent": "zcloud-worker-progress-watchdog/1"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(base_url.rstrip("/") + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            body = json.load(response)
            return int(response.status), body if isinstance(body, dict) else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            body = json.loads(raw)
        except Exception:
            body = {"error": raw[:1000]}
        return int(exc.code), body if isinstance(body, dict) else {}
    except Exception as exc:
        return 599, {"error": str(exc)[:500]}


def load_state(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            value.setdefault("workers", {})
            value.setdefault("global", {})
            return value
    except Exception:
        pass
    return {"version": 1, "workers": {}, "global": {}}


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(path)


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone() is not None


def material_signal(
    db_path: Path,
    *,
    project_id: str,
    queue_id: str,
    worker_slot: int,
) -> dict[str, Any]:
    signal: dict[str, Any] = {
        "queue_id": queue_id,
        "project_id": project_id,
        "worker_slot": worker_slot,
        "queue": None,
        "sample": None,
        "queue_result_event": None,
        "human_gate": False,
    }
    if not db_path.exists():
        signal["db_error"] = "history.db missing"
        return signal

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)
        conn.row_factory = sqlite3.Row
        try:
            if queue_id and queue_id != "NONE" and _table_exists(conn, "portfolio_queue"):
                row = conn.execute(
                    "SELECT queue_id,status,evidence,blocker,updated_at FROM portfolio_queue WHERE queue_id=?",
                    (queue_id,),
                ).fetchone()
                if row:
                    queue = dict(row)
                    signal["queue"] = {
                        "queue_id": queue.get("queue_id"),
                        "status": queue.get("status"),
                        "evidence": queue.get("evidence") or "",
                        "blocker": queue.get("blocker") or "",
                    }
                    status = str(queue.get("status") or "").strip().lower()
                    blocker = str(queue.get("blocker") or "").strip().lower()
                    signal["human_gate"] = status in HUMAN_GATE_STATUSES or blocker.startswith("wait_human")

            if _table_exists(conn, "project_samples"):
                row = conn.execute(
                    "SELECT ts,progress,commits,hash,message FROM project_samples "
                    "WHERE project=? ORDER BY ts DESC LIMIT 1",
                    (project_id,),
                ).fetchone()
                if row:
                    sample = dict(row)
                    signal["sample"] = {
                        "hash": sample.get("hash") or "",
                        "commits": sample.get("commits"),
                        "progress": sample.get("progress"),
                        "message": sample.get("message") or "",
                    }

            if _table_exists(conn, "runner_events"):
                row = conn.execute(
                    "SELECT id,ts,event,reason,error FROM runner_events "
                    "WHERE project_id=? AND worker_slot=? AND event='portfolio-queue-result' "
                    "ORDER BY id DESC LIMIT 1",
                    (project_id, worker_slot),
                ).fetchone()
                if row:
                    event = dict(row)
                    signal["queue_result_event"] = {
                        "id": event.get("id"),
                        "event": event.get("event"),
                        "reason": event.get("reason") or "",
                        "error": event.get("error") or "",
                    }
        finally:
            conn.close()
    except Exception as exc:
        signal["db_error"] = str(exc)[:500]

    return signal


def clear_stale_pending_commands(
    db_path: Path,
    *,
    project_id: str | None = None,
    now: datetime,
    min_age_seconds: int = OFFLINE_RESTART_AFTER_SECONDS,
) -> list[int]:
    """Fail stale pending browser commands so a fresh recovery command can be accepted.

    With project_id=None this is the global health reconciliation path. It uses the
    caller's age threshold and deliberately includes old project-level commands,
    because those otherwise sit outside worker-key recovery forever.
    """
    if not db_path.exists():
        return []
    cleared: list[int] = []
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA busy_timeout=5000")
            if project_id:
                rows = conn.execute(
                    "SELECT id,created_at FROM runner_commands WHERE project_id=? AND status='pending' ORDER BY id",
                    (project_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id,created_at FROM runner_commands WHERE status='pending' ORDER BY id"
                ).fetchall()
            for row in rows:
                created = parse_time(row["created_at"])
                if created is None or (now - created).total_seconds() >= min_age_seconds:
                    cleared.append(int(row["id"]))
            if cleared:
                placeholders = ",".join("?" for _ in cleared)
                conn.execute(
                    f"UPDATE runner_commands SET status='failed',updated_at=?,result=? WHERE id IN ({placeholders})",
                    (
                        now.isoformat(),
                        "worker-progress-watchdog superseded stale pending command",
                        *cleared,
                    ),
                )
                conn.commit()
        finally:
            conn.close()
    except Exception:
        return []
    return cleared


def material_fingerprint(signal: dict[str, Any]) -> str:
    material = {
        "queue_id": signal.get("queue_id"),
        "project_id": signal.get("project_id"),
        "worker_slot": signal.get("worker_slot"),
        "queue": signal.get("queue"),
        "sample": signal.get("sample"),
        "queue_result_event": signal.get("queue_result_event"),
        "human_gate": signal.get("human_gate"),
    }
    raw = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def locate_worker(status: dict[str, Any], key: str) -> dict[str, Any] | None:
    base = key.split("::w", 1)[0]
    workers = ((status.get("chatgpt_runners") or {}).get(base) or {}).get("workers") or []
    return next((item for item in workers if str(item.get("worker_id") or "") == key), None)


def latest_activity_age(worker: dict[str, Any] | None, now: datetime) -> int:
    if not worker:
        return 10**9
    ages: list[int] = []
    for field in ("last_event", "last_prompt_sent", "last_generation_started", "last_generation_finished"):
        value = worker.get(field) or {}
        age = age_seconds(value.get("time"), now)
        if age is not None:
            ages.append(age)
    return min(ages) if ages else 10**9


def seconds_since(value: Any, now: datetime) -> int:
    age = age_seconds(value, now)
    return age if age is not None else 10**9


def choose_action(
    *,
    stalled_seconds: int,
    activity_age_seconds: int,
    prompt_sent_age_seconds: int,
    runtime_state: str,
    generating: bool,
    progress_age_seconds: int | None,
    seconds_since_last_attempt: int,
    seconds_since_last_new_chat: int,
    human_gate: bool,
) -> str | None:
    """Choose the least disruptive recovery action for one allocated worker."""
    if human_gate:
        return None

    runtime_state = str(runtime_state or "offline").lower()
    if runtime_state in {"offline", "stale"}:
        if (
            stalled_seconds >= OFFLINE_RESTART_AFTER_SECONDS
            and seconds_since_last_new_chat >= NEW_CHAT_COOLDOWN_SECONDS
        ):
            return "new_chat"

    if generating:
        # Long generations are legitimate. Only intervene once the backend itself
        # reports no token/progress movement for the protection window.
        if progress_age_seconds is None or progress_age_seconds < GENERATION_PROTECT_SECONDS:
            return None

    # Browser heartbeats are not enough. If this allocated worker has not
    # actually sent a prompt for too long, refresh its conversation even when
    # generic liveness events keep arriving.
    if (
        prompt_sent_age_seconds >= PROMPT_STALE_REFRESH_SECONDS
        and stalled_seconds >= PUSH_AFTER_SECONDS
        and seconds_since_last_new_chat >= NEW_CHAT_COOLDOWN_SECONDS
    ):
        return "new_chat"

    if activity_age_seconds < QUIET_GRACE_SECONDS:
        return None

    if (
        stalled_seconds >= RESTART_AFTER_SECONDS
        and seconds_since_last_new_chat >= NEW_CHAT_COOLDOWN_SECONDS
    ):
        return "new_chat"

    if (
        stalled_seconds >= PUSH_AFTER_SECONDS
        and seconds_since_last_attempt >= ACTION_COOLDOWN_SECONDS
    ):
        return "push"

    return None


def run_once(
    *,
    db_path: Path,
    state_path: Path,
    base_url: str,
    dry_run: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or utc_now()
    state = load_state(state_path)
    stale_commands_cleared = [] if dry_run else clear_stale_pending_commands(
        db_path,
        project_id=None,
        now=now,
        min_age_seconds=STALE_PENDING_COMMAND_SECONDS,
    )

    targets_status, targets = api_call(base_url, "GET", "/api/runner-targets")
    runtime_status, runtime = api_call(base_url, "GET", "/api/status")
    if targets_status >= 300 or runtime_status >= 300:
        result = {
            "ok": False,
            "error": "zCloud control API unavailable",
            "targets_status": targets_status,
            "runtime_status": runtime_status,
            "targets_error": targets.get("error"),
            "runtime_error": runtime.get("error"),
        }
        return result

    allocation = targets.get("global_allocation") or {}
    selected = allocation.get("workers") or []
    selected_keys = {worker_key(item) for item in selected if worker_key(item)}
    memory_guard = (runtime.get("dynamic_workers") or {}).get("memory_guard") or {}
    firefox = runtime.get("chatgpt_firefox") or {}

    # A kernel OOM kill can remove the Firefox host in one instant, long before
    # per-worker stall timers fire. Recover the browser runtime immediately and
    # let the next watchdog tick resume worker-level commands.
    if selected_keys and not bool(firefox.get("active")):
        global_state = state.setdefault("global", {})
        last_restart_age = seconds_since(global_state.get("last_firefox_restart_at"), now)
        global_action = None
        if last_restart_age >= FIREFOX_RUNTIME_RESTART_COOLDOWN_SECONDS:
            if dry_run:
                global_action = {
                    "action": "restart_firefox",
                    "reason": "firefox-runtime-inactive",
                    "ok": True,
                    "dry_run": True,
                }
            else:
                status_code, payload = api_call(
                    base_url,
                    "POST",
                    "/api/runner-control",
                    {"project_id": "", "action": "restart_firefox"},
                )
                accepted = status_code < 300 and bool(payload.get("ok"))
                global_action = {
                    "action": "restart_firefox",
                    "reason": "firefox-runtime-inactive",
                    "ok": accepted,
                    "http_status": status_code,
                    "response": payload,
                }
                if accepted:
                    global_state["last_firefox_restart_at"] = now.isoformat()
        state["updated_at"] = now.isoformat()
        state["version"] = 1
        if not dry_run:
            save_state(state_path, state)
        return {
            "ok": True,
            "checked_at": now.isoformat(),
            "allocated_workers": sorted(selected_keys),
            "worker_count": len(selected_keys),
            "workers": [],
            "global_action": global_action,
            "firefox": firefox,
            "memory_guard": memory_guard,
            "reason": "firefox-runtime-inactive",
            "stale_commands_cleared": stale_commands_cleared,
            "thresholds": {
                "firefox_runtime_restart_cooldown_seconds": FIREFOX_RUNTIME_RESTART_COOLDOWN_SECONDS,
                "stale_pending_command_seconds": STALE_PENDING_COMMAND_SECONDS,
            },
        }

    # Drop old worker state once it is no longer allocated.
    for key in list((state.get("workers") or {}).keys()):
        if key not in selected_keys:
            state["workers"].pop(key, None)

    reports: list[dict[str, Any]] = []
    global_recovery_candidates: list[dict[str, Any]] = []

    for item in selected:
        key = worker_key(item)
        if not key:
            continue
        project_id = str(item.get("project_id") or key.split("::w", 1)[0])
        try:
            slot = max(1, int(item.get("worker_slot") or (key.split("::w", 1)[1] if "::w" in key else 1)))
        except Exception:
            slot = 1
        queue_id = str(item.get("queue_id") or "NONE")

        signal = material_signal(
            db_path,
            project_id=project_id,
            queue_id=queue_id,
            worker_slot=slot,
        )
        fingerprint = material_fingerprint(signal)
        worker_state = (state.setdefault("workers", {})).setdefault(key, {})

        previous_fingerprint = str(worker_state.get("material_fingerprint") or "")
        previous_queue = str(worker_state.get("queue_id") or "")
        changed = fingerprint != previous_fingerprint or queue_id != previous_queue
        if changed:
            worker_state.update({
                "material_fingerprint": fingerprint,
                "material_seen_at": now.isoformat(),
                "queue_id": queue_id,
                "last_material_signal": signal,
                "new_chat_count_since_progress": 0,
            })

        material_seen_at = worker_state.get("material_seen_at") or now.isoformat()
        stalled_seconds = seconds_since(material_seen_at, now)

        live_worker = locate_worker(runtime, key)
        runtime_state = str((live_worker or {}).get("state") or "offline")
        generating = bool((live_worker or {}).get("generating"))
        progress_age = (live_worker or {}).get("progress_age_seconds")
        try:
            progress_age_int = int(progress_age) if progress_age is not None else None
        except Exception:
            progress_age_int = None
        activity_age = latest_activity_age(live_worker, now)
        last_prompt_sent = ((live_worker or {}).get("last_prompt_sent") or {}).get("time")
        prompt_sent_age = seconds_since(last_prompt_sent, now)
        worker_state["last_prompt_sent_at"] = last_prompt_sent
        worker_state["last_prompt_sent_age_seconds"] = prompt_sent_age

        last_attempt_age = seconds_since(worker_state.get("last_attempt_at"), now)
        last_new_chat_age = seconds_since(worker_state.get("last_new_chat_at"), now)
        action = choose_action(
            stalled_seconds=stalled_seconds,
            activity_age_seconds=activity_age,
            prompt_sent_age_seconds=prompt_sent_age,
            runtime_state=runtime_state,
            generating=generating,
            progress_age_seconds=progress_age_int,
            seconds_since_last_attempt=last_attempt_age,
            seconds_since_last_new_chat=last_new_chat_age,
            human_gate=bool(signal.get("human_gate")),
        )

        action_result: dict[str, Any] | None = None
        if action:
            worker_state["last_attempt_at"] = now.isoformat()
            cleared_pending: list[int] = []
            if dry_run:
                action_result = {"ok": True, "dry_run": True, "action": action, "cleared_pending": []}
            else:
                cleared_pending = clear_stale_pending_commands(
                    db_path,
                    project_id=key,
                    now=now,
                )
                status_code, payload = api_call(
                    base_url,
                    "POST",
                    "/api/runner-control",
                    {"project_id": key, "action": action},
                )
                accepted = status_code < 300 and bool(payload.get("ok"))
                action_result = {
                    "ok": accepted,
                    "http_status": status_code,
                    "action": action,
                    "response": payload,
                    "cleared_pending": cleared_pending,
                }
                if accepted:
                    worker_state["last_action"] = action
                    worker_state["last_action_at"] = now.isoformat()
                    if action == "new_chat":
                        worker_state["last_new_chat_at"] = now.isoformat()
                        worker_state["new_chat_count_since_progress"] = int(
                            worker_state.get("new_chat_count_since_progress") or 0
                        ) + 1

        report = {
            "worker_key": key,
            "project_id": project_id,
            "provider": item.get("provider"),
            "queue_id": queue_id,
            "material_changed": changed,
            "material_stalled_seconds": stalled_seconds,
            "activity_age_seconds": activity_age,
            "last_prompt_sent_at": last_prompt_sent,
            "last_prompt_sent_age_seconds": prompt_sent_age,
            "runtime_state": runtime_state,
            "generating": generating,
            "progress_age_seconds": progress_age_int,
            "human_gate": bool(signal.get("human_gate")),
            "action": action,
            "action_result": action_result,
            "new_chat_count_since_progress": int(worker_state.get("new_chat_count_since_progress") or 0),
        }
        reports.append(report)

        if (
            not signal.get("human_gate")
            and stalled_seconds >= GLOBAL_RECOVERY_AFTER_SECONDS
            and not generating
            and int(worker_state.get("new_chat_count_since_progress") or 0) >= 2
        ):
            global_recovery_candidates.append(report)

    global_action = None
    if selected_keys and len(global_recovery_candidates) == len(selected_keys):
        global_state = state.setdefault("global", {})
        if seconds_since(global_state.get("last_firefox_restart_at"), now) >= GLOBAL_RECOVERY_COOLDOWN_SECONDS:
            if dry_run:
                global_action = {"action": "restart_firefox", "ok": True, "dry_run": True}
            else:
                status_code, payload = api_call(
                    base_url,
                    "POST",
                    "/api/runner-control",
                    {"project_id": "", "action": "restart_firefox"},
                )
                accepted = status_code < 300 and bool(payload.get("ok"))
                global_action = {
                    "action": "restart_firefox",
                    "ok": accepted,
                    "http_status": status_code,
                    "response": payload,
                }
                if accepted:
                    global_state["last_firefox_restart_at"] = now.isoformat()

    state["updated_at"] = now.isoformat()
    state["version"] = 1
    if not dry_run:
        save_state(state_path, state)

    return {
        "ok": True,
        "checked_at": now.isoformat(),
        "allocated_workers": sorted(selected_keys),
        "worker_count": len(selected_keys),
        "workers": reports,
        "global_action": global_action,
        "firefox": firefox,
        "memory_guard": memory_guard,
        "stale_commands_cleared": stale_commands_cleared,
        "thresholds": {
            "push_after_seconds": PUSH_AFTER_SECONDS,
            "restart_after_seconds": RESTART_AFTER_SECONDS,
            "offline_restart_after_seconds": OFFLINE_RESTART_AFTER_SECONDS,
            "quiet_grace_seconds": QUIET_GRACE_SECONDS,
            "generation_protect_seconds": GENERATION_PROTECT_SECONDS,
            "prompt_stale_refresh_seconds": PROMPT_STALE_REFRESH_SECONDS,
            "global_recovery_after_seconds": GLOBAL_RECOVERY_AFTER_SECONDS,
            "firefox_runtime_restart_cooldown_seconds": FIREFOX_RUNTIME_RESTART_COOLDOWN_SECONDS,
            "stale_pending_command_seconds": STALE_PENDING_COMMAND_SECONDS,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="zCloud dynamic-worker material-progress watchdog")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--state-file", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--clear-stale-pending-only", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.clear_stale_pending_only:
        cleared = clear_stale_pending_commands(
            args.db,
            project_id=None,
            now=utc_now(),
            min_age_seconds=STALE_PENDING_COMMAND_SECONDS,
        )
        result = {
            "ok": True,
            "cleared_command_ids": cleared,
            "threshold_seconds": STALE_PENDING_COMMAND_SECONDS,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2 if args.json else None, sort_keys=True))
        return 0

    result = run_once(
        db_path=args.db,
        state_path=args.state_file,
        base_url=args.base_url,
        dry_run=args.dry_run,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Read-only health contract for the zCloud control plane."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_ROOT = Path(os.environ.get("ZCLOUD_ROOT", "/home/ubuntu/zennay-cloud"))
DEFAULT_DB = Path(os.environ.get("ZCLOUD_DB", str(DEFAULT_ROOT / "history.db")))
DEFAULT_BASE_URL = os.environ.get("ZCLOUD_BASE_URL", "http://127.0.0.1:8765")
DEEP_STATUS_TIMEOUT_SECONDS = 40.0
FAST_API_TIMEOUT_SECONDS = 5.0
DEFAULT_RUNTIME_EXTENSION = Path(os.environ.get(
    "ZCLOUD_FIREFOX_RUNTIME_EXTENSION",
    str(Path.home() / "snap/firefox/common/chatgpt-project-extension/background.js"),
))
DEFAULT_FIREFOX_LEGACY_DISABLE = Path(os.environ.get(
    "ZCLOUD_FIREFOX_LEGACY_DISABLE",
    str(Path.home() / ".config/systemd/user/chatgpt-firefox.service.d/10-legacy-disabled.conf"),
))
REQUIRED_TABLES = {
    "runner_targets",
    "runner_workers",
    "runner_commands",
    "runner_events",
    "task_claims",
    "improvement_loops",
    "config_audit",
    "feature_flags",
    "worker_preflights",
}
VALID_DESIRED_STATES = {"running", "paused", "draining"}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def service_active(service: str, *, user: bool = False) -> bool:
    cmd = ["systemctl"]
    env = os.environ.copy()
    if user:
        cmd.append("--user")
        env.update({
            "XDG_RUNTIME_DIR": "/run/user/1000",
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
        })
    cmd.extend(["is-active", "--quiet", service])
    return subprocess.run(cmd, env=env).returncode == 0


def http_json(url: str, timeout: float = 3.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status}: {url}")
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"non-object JSON: {url}")
    return payload


def inspect_store(
    db_path: Path,
    *,
    max_pending_age_seconds: int = 300,
    now: datetime | None = None,
) -> dict:
    now = now or utc_now()
    result = {
        "ok": False,
        "quick_check": None,
        "tables": [],
        "missing_tables": [],
        "targets": [],
        "workers": [],
        "problems": [],
    }
    if not db_path.exists():
        result["problems"].append("history.db missing")
        return result
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)
        conn.row_factory = sqlite3.Row
        quick = [row[0] for row in conn.execute("PRAGMA quick_check").fetchall()]
        result["quick_check"] = quick
        tables = {
            str(row["name"])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        result["tables"] = sorted(tables)
        missing = sorted(REQUIRED_TABLES - tables)
        result["missing_tables"] = missing
        if quick != ["ok"]:
            result["problems"].append("SQLite quick_check failed")
        if missing:
            result["problems"].append("missing required tables: " + ",".join(missing))
        if missing:
            conn.close()
            return result

        targets = [
            dict(row) for row in conn.execute(
                "SELECT project_id,active,worker_count,conversation_id "
                "FROM runner_targets ORDER BY project_id"
            )
        ]
        workers = [
            dict(row) for row in conn.execute(
                "SELECT project_id,worker_slot,desired_state,conversation_id "
                "FROM runner_workers ORDER BY project_id,worker_slot"
            )
        ]
        result["targets"] = targets
        result["workers"] = workers

        target_ids = {str(row["project_id"]) for row in targets}
        by_project: dict[str, dict[int, dict]] = {}
        for row in workers:
            pid = str(row["project_id"])
            slot = int(row["worker_slot"])
            by_project.setdefault(pid, {})[slot] = row
            if pid not in target_ids:
                result["problems"].append(f"orphan runner worker: {pid}::{slot}")
            if str(row["desired_state"]) not in VALID_DESIRED_STATES:
                result["problems"].append(
                    f"invalid desired_state for {pid}::{slot}: {row['desired_state']}"
                )

        for target in targets:
            pid = str(target["project_id"])
            count = int(target["worker_count"] or 0)
            if count < 1:
                result["problems"].append(f"invalid worker_count for {pid}: {count}")
                continue
            slots = by_project.get(pid, {})
            missing_slots = [slot for slot in range(1, count + 1) if slot not in slots]
            if missing_slots:
                result["problems"].append(
                    f"missing desired worker slots for {pid}: {missing_slots}"
                )

        pending_rows = conn.execute(
            "SELECT id,project_id,action,created_at FROM runner_commands "
            "WHERE status='pending' ORDER BY id"
        ).fetchall()
        stale_pending = []
        for row in pending_rows:
            try:
                age = (now - parse_time(row["created_at"])).total_seconds()
            except Exception:
                age = max_pending_age_seconds + 1
            if age > max_pending_age_seconds:
                stale_pending.append({
                    "id": row["id"],
                    "project_id": row["project_id"],
                    "action": row["action"],
                    "age_seconds": round(age),
                })
        result["stale_pending_commands"] = stale_pending
        if stale_pending:
            result["problems"].append(
                f"{len(stale_pending)} pending runner command(s) older than "
                f"{max_pending_age_seconds}s"
            )
        conn.close()
    except Exception as exc:
        result["problems"].append(f"state-store read failed: {exc}")
    result["ok"] = not result["problems"]
    return result


def evaluate(
    status: dict,
    targets_payload: dict,
    store: dict,
    *,
    zcloud_service: bool,
    firefox_service: bool,
    source_runtime_match: bool,
    legacy_violentmonkey_only: bool = False,
) -> dict:
    checks: list[dict] = []

    def add(name: str, ok: bool, detail) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    add("webservice_process", zcloud_service, zcloud_service)
    add("webservice_api", isinstance(status, dict), "status JSON object")
    add("webservice_errors", not (status.get("errors") or []), status.get("errors") or [])

    firefox = status.get("chatgpt_firefox") or {}
    firefox_runtime_active = firefox.get("active") is True or firefox.get("state") == "active"
    firefox_service_ok = firefox_service or legacy_violentmonkey_only
    # The legacy marker only excuses the intentionally disabled systemd unit.
    # A standalone Violentmonkey Firefox process must still be alive; otherwise
    # an OOM-killed browser could be reported healthy.
    firefox_runtime_ok = firefox_runtime_active
    add("firefox_service", firefox_service_ok, {
        "active": firefox_service,
        "legacy_violentmonkey_only": legacy_violentmonkey_only,
    })
    add("firefox_runtime", firefox_runtime_ok, {
        "runtime": firefox,
        "legacy_violentmonkey_only": legacy_violentmonkey_only,
    })
    add("firefox_source_runtime_match", source_runtime_match, source_runtime_match)

    memory_guard = (status.get("dynamic_workers") or {}).get("memory_guard") or {}
    memory_pressure = str(memory_guard.get("pressure") or "unknown").lower()
    memory_ok = memory_pressure in {"ok", "warning", "guarded"}
    add("worker_memory", memory_ok, {
        "pressure": memory_pressure,
        "available_mb": memory_guard.get("available_mb"),
        "headroom_mb": memory_guard.get("headroom_mb"),
        "effective_headroom_mb": memory_guard.get("effective_headroom_mb"),
        "new_worker_capacity": memory_guard.get("new_worker_capacity"),
        "swap_total_mb": memory_guard.get("swap_total_mb"),
        "swap_free_mb": memory_guard.get("swap_free_mb"),
        "swap_healthy": memory_guard.get("swap_healthy"),
    })

    add("project_state_store", store.get("ok") is True, {
        "quick_check": store.get("quick_check"),
        "missing_tables": store.get("missing_tables"),
        "problems": store.get("problems"),
    })

    runners = status.get("chatgpt_runners") or {}
    projects = targets_payload.get("projects")
    max_workers = targets_payload.get("max_workers")
    scheduler_problems = []
    if not isinstance(projects, dict):
        scheduler_problems.append("runner-targets projects missing")
        projects = {}
    try:
        max_workers_int = int(max_workers)
        if max_workers_int < 1:
            raise ValueError
    except Exception:
        max_workers_int = 0
        scheduler_problems.append("invalid max_workers")

    for target in store.get("targets") or []:
        pid = str(target["project_id"])
        count = int(target["worker_count"] or 0)
        if max_workers_int and count > max_workers_int:
            scheduler_problems.append(
                f"{pid}: worker_count {count} exceeds max_workers {max_workers_int}"
            )
        runner = runners.get(pid)
        if not isinstance(runner, dict):
            scheduler_problems.append(f"{pid}: missing live runner read-model")
            continue
        if int(runner.get("desired_worker_count") or 0) != count:
            scheduler_problems.append(
                f"{pid}: desired count mismatch "
                f"{runner.get('desired_worker_count')} != {count}"
            )
        expected_slots = set(range(1, count + 1))
        actual_slots = {
            int(worker.get("worker_slot") or 0)
            for worker in (runner.get("workers") or [])
        }
        if not expected_slots.issubset(actual_slots):
            scheduler_problems.append(
                f"{pid}: missing live slots {sorted(expected_slots - actual_slots)}"
            )
        if bool(runner.get("active")) != bool(target["active"]):
            scheduler_problems.append(
                f"{pid}: active-state mismatch "
                f"{runner.get('active')} != {bool(target['active'])}"
            )
        api_slots = {
            int(item.get("worker_slot") or 0)
            for item in projects.values()
            if isinstance(item, dict)
            and str(item.get("base_project_id") or item.get("project_id") or "") == pid
        }
        if api_slots != expected_slots:
            scheduler_problems.append(
                f"{pid}: runner-target slots {sorted(api_slots)} != "
                f"{sorted(expected_slots)}"
            )

    if store.get("stale_pending_commands"):
        scheduler_problems.append("stale pending runner command(s) present")

    add("worker_scheduler", not scheduler_problems, scheduler_problems or "consistent")

    return {
        "ok": all(check["ok"] for check in checks),
        "checks": checks,
        "summary": {
            "webservice": "healthy" if all(
                x["ok"] for x in checks if x["name"].startswith("webservice_")
            ) else "problem",
            "firefox_automation": "healthy" if all(
                x["ok"] for x in checks if x["name"].startswith("firefox_")
            ) else "problem",
            "worker_scheduler": "healthy" if next(
                x["ok"] for x in checks if x["name"] == "worker_scheduler"
            ) else "problem",
            "worker_memory": "healthy" if next(
                x["ok"] for x in checks if x["name"] == "worker_memory"
            ) else "critical",
            "project_state_store": "healthy" if next(
                x["ok"] for x in checks if x["name"] == "project_state_store"
            ) else "problem",
        },
    }


def legacy_violentmonkey_only(path: Path = DEFAULT_FIREFOX_LEGACY_DISABLE) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    lowered = text.lower()
    return "violentmonkey only" in lowered and "execcondition=/bin/false" in lowered


def live_health(
    *,
    root: Path = DEFAULT_ROOT,
    db_path: Path = DEFAULT_DB,
    base_url: str = DEFAULT_BASE_URL,
    runtime_extension: Path = DEFAULT_RUNTIME_EXTENSION,
    legacy_disable_path: Path = DEFAULT_FIREFOX_LEGACY_DISABLE,
    max_pending_age_seconds: int = 300,
) -> dict:
    transport_errors = []
    try:
        status = http_json(
            base_url.rstrip("/") + "/api/status",
            timeout=DEEP_STATUS_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        status = {"errors": [f"status unavailable: {exc}"]}
        transport_errors.append(str(exc))
    try:
        targets = http_json(
            base_url.rstrip("/") + "/api/runner-targets",
            timeout=FAST_API_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        targets = {}
        transport_errors.append(str(exc))

    source = root / "firefox-extension/background.js"
    source_runtime_match = (
        source.exists()
        and runtime_extension.exists()
        and sha256_file(source) == sha256_file(runtime_extension)
    )
    store = inspect_store(
        db_path,
        max_pending_age_seconds=max_pending_age_seconds,
    )
    result = evaluate(
        status,
        targets,
        store,
        zcloud_service=service_active("zennay-cloud.service"),
        firefox_service=service_active("chatgpt-firefox.service", user=True),
        source_runtime_match=source_runtime_match,
        legacy_violentmonkey_only=legacy_violentmonkey_only(legacy_disable_path),
    )
    result["store"] = store
    if transport_errors:
        result["transport_errors"] = transport_errors
        result["ok"] = False
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only zCloud health contract")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--runtime-extension", type=Path, default=DEFAULT_RUNTIME_EXTENSION)
    parser.add_argument("--max-pending-age-seconds", type=int, default=300)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result = live_health(
        root=args.root.resolve(),
        db_path=args.db.resolve(),
        base_url=args.base_url,
        runtime_extension=args.runtime_extension.resolve(),
        max_pending_age_seconds=max(30, args.max_pending_age_seconds),
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for check in result["checks"]:
            print(("OK" if check["ok"] else "FAIL").ljust(5), check["name"], check["detail"])
        print("HEALTH_GREEN" if result["ok"] else "HEALTH_BLOCKED")
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

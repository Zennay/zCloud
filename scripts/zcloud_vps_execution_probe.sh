#!/usr/bin/env bash
set -euo pipefail
# Read-only live worker verification is intentionally kept in this probe.
# Post-activation verification run.

# Non-destructive capability probe for the shared zCloud VPS execution lane.
# This is intentionally a probe, not an arbitrary remote-command endpoint.

host="$(hostname -s 2>/dev/null || hostname)"
user="$(id -un)"
runner_pid="$$"

has_cmd() {
  command -v "$1" >/dev/null 2>&1
}

optional_version() {
  if has_cmd "$1"; then
    "$@" 2>&1 | head -n 1 | tr '\n' ' ' || true
  else
    printf 'missing'
  fi
}

printf 'ZCLOUD_VPS_EXECUTION_PROBE host=%s user=%s pid=%s\n' "$host" "$user" "$runner_pid"
printf 'ZCLOUD_VPS_CAPABILITY ssh=%s\n' "$(has_cmd ssh && echo yes || echo no)"
printf 'ZCLOUD_VPS_CAPABILITY git=%s\n' "$(has_cmd git && echo yes || echo no)"
printf 'ZCLOUD_VPS_CAPABILITY python3=%s\n' "$(has_cmd python3 && echo yes || echo no)"
printf 'ZCLOUD_VPS_CAPABILITY systemctl=%s\n' "$(has_cmd systemctl && echo yes || echo no)"
printf 'ZCLOUD_VPS_CAPABILITY docker=%s\n' "$(has_cmd docker && echo yes || echo no)"
printf 'ZCLOUD_VPS_CAPABILITY sudo_noninteractive=%s\n' "$(sudo -n true >/dev/null 2>&1 && echo yes || echo no)"
printf 'ZCLOUD_VPS_VERSION ssh=%s\n' "$(optional_version ssh -V)"
printf 'ZCLOUD_VPS_VERSION git=%s\n' "$(optional_version git --version)"
printf 'ZCLOUD_VPS_VERSION python3=%s\n' "$(optional_version python3 --version)"

printf 'ZCLOUD_RUNNER_STATUS_BEGIN\n'
python3 - <<'PY'
import json
import urllib.request

try:
    with urllib.request.urlopen("http://127.0.0.1:8765/api/status?project=zcloud", timeout=8) as response:
        status = json.load(response)
except Exception as exc:
    print(json.dumps({"status": "unavailable", "error": str(exc)[:200]}, ensure_ascii=False))
else:
    runner = (status.get("chatgpt_runners") or {}).get("raiseai") or {}
    workers = runner.get("workers") or []
    print(json.dumps({
        "runner_state": runner.get("state"),
        "runner_last_prompt_sent": runner.get("last_prompt_sent"),
        "runner_last_generation_started": runner.get("last_generation_started"),
        "runner_last_generation_finished": runner.get("last_generation_finished"),
        "workers": [
            {
                "worker_id": worker.get("worker_id"),
                "state": worker.get("state"),
                "active": worker.get("active"),
                "age_seconds": worker.get("age_seconds"),
                "last_event": worker.get("last_event"),
                "last_heartbeat": worker.get("last_heartbeat"),
                "current_task": worker.get("current_task"),
                "command": worker.get("command"),
            }
            for worker in workers
        ],
    }, ensure_ascii=False, sort_keys=True))
PY
printf 'ZCLOUD_RUNNER_STATUS_END\n'

printf 'ZCLOUD_DISPATCH_DB_BEGIN\n'
python3 - <<'PY'
import json
import sqlite3

db_path = "/home/ubuntu/zennay-cloud/history.db"
try:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    runtime = conn.execute(
        "SELECT project_id,last_dispatch_at,last_reason,manual_pause "
        "FROM autonomy_runtime WHERE project_id='raiseai'"
    ).fetchone()
    commands = conn.execute(
        "SELECT id,project_id,action,status,created_at,updated_at,result "
        "FROM runner_commands WHERE project_id IN ('raiseai','raiseai::w1') "
        "ORDER BY id DESC LIMIT 12"
    ).fetchall()
    events = conn.execute(
        "SELECT id,ts,event,reason,generating,sending,error "
        "FROM runner_events WHERE project_id='raiseai' "
        "ORDER BY id DESC LIMIT 20"
    ).fetchall()
    print(json.dumps({
        "autonomy_runtime": dict(runtime) if runtime else None,
        "recent_commands": [dict(row) for row in commands],
        "recent_events": [dict(row) for row in events],
    }, ensure_ascii=False, sort_keys=True))
    conn.close()
except Exception as exc:
    print(json.dumps({"error": str(exc)[:300]}, ensure_ascii=False))
PY
printf 'ZCLOUD_DISPATCH_DB_END\n'

printf 'ZCLOUD_AUTONOMY_API_BEGIN\n'
python3 - <<'PY'
import json
import urllib.request

for path in ("/api/autonomy", "/api/runner-targets"):
    try:
        with urllib.request.urlopen("http://127.0.0.1:8765" + path, timeout=8) as response:
            payload = json.load(response)
        if path == "/api/autonomy":
            payload = {
                "raiseai": (payload.get("projects") or {}).get("raiseai"),
            }
        else:
            allocation = payload.get("global_allocation") or {}
            payload = {
                "max_workers": payload.get("max_workers"),
                "allocation": {
                    "workers": allocation.get("workers"),
                    "projects": allocation.get("projects"),
                    "dispatch_cooldown_seconds": allocation.get("dispatch_cooldown_seconds"),
                },
                "raiseai_target": (payload.get("projects") or {}).get("raiseai::w1"),
            }
        print(json.dumps({"path": path, "payload": payload}, ensure_ascii=False, sort_keys=True))
    except Exception as exc:
        print(json.dumps({"path": path, "error": str(exc)[:300]}, ensure_ascii=False))
PY
printf 'ZCLOUD_AUTONOMY_API_END\n'

printf 'ZCLOUD_SCHEDULER_LOG_BEGIN\n'
journalctl -u zennay-cloud.service -n 120 --no-pager 2>/dev/null | grep -E 'Autonomy|scheduler|push|worker|ERROR|Exception' | tail -n 80 || true
printf 'ZCLOUD_SCHEDULER_LOG_END\n'
printf 'ZCLOUD_VPS_PROBE=GREEN\n'

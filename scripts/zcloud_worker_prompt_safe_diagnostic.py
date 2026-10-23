"""Bounded, privacy-preserving SQLite worker diagnostic for zCloud.

Do NOT call /api/runner-targets or /api/worker-debug: issue #1341 proves their
nominal GET handlers may reconcile SQLite. The only data source here is a
local read-only SQLite connection with PRAGMA query_only=ON. No worker IDs,
prompt/session data, event content, command arguments or process argv escape.
"""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
from urllib.parse import quote

DB = Path("/home/ubuntu/zennay-cloud/history.db")
MAX_ROWS = 400
SAFE_EVENT_TYPES = (
    "prompt-sent", "generation-started", "generation-progress", "generation-finished",
)
SAFE_COMMAND_STATES = ("pending", "completed", "failed")


def _count_allowed(values, allowed):
    return {key.replace("-", "_"): sum(value == key for value in values) for key in allowed}


def summarize_rows(worker_count, commands, events):
    """Reduce a bounded database snapshot to fixed names and numeric values."""
    if type(worker_count) is not int or worker_count < 0 or worker_count > 10000:
        raise ValueError("invalid worker count")
    for collection in (commands, events):
        if not isinstance(collection, list) or len(collection) > MAX_ROWS:
            raise ValueError("unbounded query result")
        if any(not isinstance(value, str) for value in collection):
            raise ValueError("invalid query item")
    result = {
        "schema": "zcloud-worker-prompt-safe-sqlite-v2",
        "direct_sqlite_read_only": True,
        "mutation_performed": False,
        "registered_workers": worker_count,
        "sampled_commands": len(commands),
        "sampled_events": len(events),
    }
    for name, count in _count_allowed(commands, SAFE_COMMAND_STATES).items():
        result["commands_" + name] = count
    for name, count in _count_allowed(events, SAFE_EVENT_TYPES).items():
        result["events_" + name] = count
    return result


def read_sqlite(path=DB):
    """Run only SELECT under read-only connection, refusing missing/alias DB."""
    path = Path(path)
    if not path.is_file() or path.is_symlink():
        raise ValueError("untrusted DB path")
    uri = "file:" + quote(str(path.absolute()), safe="/") + "?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=5) as conn:
        conn.execute("PRAGMA query_only=ON")
        if conn.execute("PRAGMA query_only").fetchone()[0] != 1:
            raise ValueError("SQLite read-only mode is not active")
        conn.execute("BEGIN")
        workers = conn.execute("SELECT COUNT(*) FROM runner_workers").fetchone()[0]
        commands = [row[0] for row in conn.execute(
            "SELECT status FROM runner_commands ORDER BY id DESC LIMIT ?", (MAX_ROWS,)
        ).fetchall()]
        events = [row[0] for row in conn.execute(
            "SELECT event FROM runner_events ORDER BY id DESC LIMIT ?", (MAX_ROWS,)
        ).fetchall()]
        return summarize_rows(workers, commands, events)


def local_host_summary():
    """Return booleans only; never disclose PID or process command-line."""
    result = subprocess.run(
        ["pgrep", "-u", str(os.getuid()), "-f", "/snap/firefox/.*/usr/lib/firefox/firefox"],
        capture_output=True, timeout=8, check=False,
    )
    if result.returncode not in (0, 1):
        raise RuntimeError("process inventory unavailable")
    return {
        "firefox_process_seen": result.returncode == 0,
        "controller_source_present": Path(
            "/home/ubuntu/snap/firefox/common/chatgpt-project-extension/worker-controller-v2.js"
        ).is_file(),
        "worker_source_present": Path(
            "/home/ubuntu/zennay-cloud/public/zcloud-worker.user.js"
        ).is_file(),
    }


def main():
    summary = read_sqlite()
    summary.update(local_host_summary())
    print("ZCLOUD_SANITIZED_WORKER_DIAGNOSTIC=" + json.dumps(
        summary, sort_keys=True, separators=(",", ":")
    ))


if __name__ == "__main__":
    main()

#!/bin/sh
set -eu

ROOT=$(git rev-parse --show-toplevel)
STATE_ROOT=${ULAB_ZCLOUD_STATE_ROOT:-${TMPDIR:-/tmp}/zcloud-ulab-dogfood}
SAFE_SOURCE=$(printf '%s' "${ULAB_SOURCE_VERSION:?ULAB_SOURCE_VERSION is required}" | tr -c 'A-Za-z0-9._-' '_')
SAFE_TARGET=$(printf '%s' "${ULAB_TARGET_VERSION:?ULAB_TARGET_VERSION is required}" | tr -c 'A-Za-z0-9._-' '_')
SAFE_RUN=$(printf '%s' "${ULAB_RUN_ID:?ULAB_RUN_ID is required}" | tr -c 'A-Za-z0-9._-' '_')
WORK="$STATE_ROOT/${SAFE_RUN}__${SAFE_SOURCE}__${SAFE_TARGET}"
SOURCE_DIR="$WORK/source"
TARGET_DIR="$WORK/target"

archive_revision() {
    revision=$1
    destination=$2
    mkdir -p "$destination"
    git -C "$ROOT" archive "$revision" | tar -x -C "$destination"
}

case "${1:-}" in
setup)
    rm -rf "$WORK"
    mkdir -p "$WORK"
    archive_revision "$ULAB_SOURCE_VERSION" "$SOURCE_DIR"
    git -C "$ROOT" rev-parse "${ULAB_SOURCE_VERSION}^{commit}" > "$WORK/source.sha"
    (
        cd "$SOURCE_DIR"
        python3 - <<'PY'
from datetime import datetime, timezone
import sqlite3
import server

server.init_db()
with sqlite3.connect(server.DB) as conn:
    conn.execute(
        "INSERT OR REPLACE INTO events(id,ts,project,kind,title,detail) VALUES(?,?,?,?,?,?)",
        (
            "ulab-zcloud-dogfood-sentinel",
            datetime.now(timezone.utc).isoformat(),
            "cloud",
            "ulab-dogfood",
            "uLab real-project compatibility sentinel",
            "Must survive the historical zCloud -> target zCloud schema upgrade.",
        ),
    )
    conn.commit()
print(server.DB)
PY
    )
    test -f "$SOURCE_DIR/history.db"
    ;;
upgrade)
    test -f "$SOURCE_DIR/history.db"
    rm -rf "$TARGET_DIR"
    archive_revision "$ULAB_TARGET_VERSION" "$TARGET_DIR"
    git -C "$ROOT" rev-parse "${ULAB_TARGET_VERSION}^{commit}" > "$WORK/target.sha"
    cp "$SOURCE_DIR/history.db" "$TARGET_DIR/history.db"
    (
        cd "$TARGET_DIR"
        python3 - <<'PY'
import server
server.init_db()
print(server.DB)
PY
    )
    ;;
verify)
    test -f "$TARGET_DIR/history.db"
    (
        cd "$TARGET_DIR"
        python3 - <<'PY'
import sqlite3
import server

# Running initialization a second time is an intentional idempotence check.
server.init_db()

expected_tables = {
    "events",
    "runner_events",
    "runner_targets",
    "runner_workers",
    "ai_global_slots",
    "runner_commands",
    "task_claims",
    "portfolio_queue",
    "portfolio_attention",
    "worker_preflights",
    "improvement_loops",
    "config_audit",
    "feature_flags",
    "runtime_settings",
    "autonomy_runtime",
}
expected_columns = {
    "runner_events": {"project_id", "progress_at", "assistant_chars", "worker_slot"},
    "runner_workers": {"desired_state", "provider"},
    "portfolio_queue": {"worker_slot", "parent_queue_id", "metadata_json"},
}

with sqlite3.connect(server.DB) as conn:
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    missing_tables = sorted(expected_tables - tables)
    if missing_tables:
        raise SystemExit("missing migrated tables: " + ", ".join(missing_tables))

    for table, required in expected_columns.items():
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        missing = sorted(required - columns)
        if missing:
            raise SystemExit(f"{table} missing migrated columns: {', '.join(missing)}")

    sentinel = conn.execute(
        "SELECT project,kind,title,detail FROM events WHERE id=?",
        ("ulab-zcloud-dogfood-sentinel",),
    ).fetchone()
    if sentinel is None:
        raise SystemExit("historical zCloud state sentinel was lost during upgrade")
    if sentinel[0] != "cloud" or sentinel[1] != "ulab-dogfood":
        raise SystemExit("historical zCloud state sentinel changed unexpectedly")

    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        raise SystemExit("SQLite integrity_check failed: " + str(integrity))

print("zCloud real-project upgrade verified: state preserved, schema complete, init_db idempotent")
PY
    )
    ;;
*)
    echo "usage: $0 setup|upgrade|verify" >&2
    exit 2
    ;;
esac

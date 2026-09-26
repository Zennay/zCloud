#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT_DIR/scripts/birds_eye_review.sh"

bash -n "$SCRIPT"
! grep -Eq 'queue_command[[:space:]]+(pause|stop|drain|new_chat)' "$SCRIPT"
! grep -Eq "runner_commands.*'(pause|stop|drain|new_chat)'" "$SCRIPT"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

cat > "$tmp/projects.json" <<JSON
[
  {"id":"cloud"},
  {"id":"ftmo"},
  {"id":"haxlab"}
]
JSON
cat > "$tmp/project-layout.json" <<JSON
{"archived":["haxlab"]}
JSON

sqlite3 "$tmp/history.db" <<SQL
CREATE TABLE events(ts TEXT, project TEXT);
CREATE TABLE runner_events(ts TEXT, project_id TEXT, event TEXT);
CREATE TABLE runner_targets(project_id TEXT PRIMARY KEY,name TEXT,conversation_id TEXT,prompt TEXT,active INTEGER,worker_count INTEGER);
CREATE TABLE runner_workers(project_id TEXT,worker_slot INTEGER,conversation_id TEXT,UNIQUE(project_id,worker_slot));
CREATE TABLE runner_commands(id INTEGER PRIMARY KEY AUTOINCREMENT,project_id TEXT,action TEXT,status TEXT,created_at TEXT,updated_at TEXT,result TEXT);
INSERT INTO runner_targets VALUES('cloud','Cloud','cloud-chat','cloud prompt',1,2);
INSERT INTO runner_targets VALUES('ftmo','FTMO','ftmo-chat','ftmo prompt',0,3);
INSERT INTO events VALUES(strftime('%Y-%m-%dT%H:%M:%f+00:00','now'),'cloud');
INSERT INTO runner_events VALUES(strftime('%Y-%m-%dT%H:%M:%f+00:00','now'),'ftmo','generation-finished');
INSERT INTO runner_events VALUES(strftime('%Y-%m-%dT%H:%M:%f+00:00','now'),'haxlab','generation-finished');
SQL

before_cloud="$(sqlite3 "$tmp/history.db" "SELECT active||':'||worker_count||':'||conversation_id FROM runner_targets WHERE project_id='cloud';")"
before_ftmo="$(sqlite3 "$tmp/history.db" "SELECT active||':'||worker_count||':'||conversation_id FROM runner_targets WHERE project_id='ftmo';")"

ZCLOUD_ROOT="$tmp" ZCLOUD_DB="$tmp/history.db" ZCLOUD_PROJECTS="$tmp/projects.json" ZCLOUD_LAYOUT="$tmp/project-layout.json" ZCLOUD_BIRDSEYE_DRY_RUN=1 "$SCRIPT"

prompt="$(sqlite3 "$tmp/history.db" "SELECT prompt FROM runner_targets WHERE project_id='portfolio-review';")"
grep -q "cloud" <<<"$prompt"
grep -q "ftmo" <<<"$prompt"
! grep -q "haxlab" <<<"$prompt"
grep -q "stop, pauzeer, drain of herstart GEEN gewone projecten" <<<"$prompt"

[[ "$before_cloud" == "$(sqlite3 "$tmp/history.db" "SELECT active||':'||worker_count||':'||conversation_id FROM runner_targets WHERE project_id='cloud';")" ]]
[[ "$before_ftmo" == "$(sqlite3 "$tmp/history.db" "SELECT active||':'||worker_count||':'||conversation_id FROM runner_targets WHERE project_id='ftmo';")" ]]
[[ "$(sqlite3 "$tmp/history.db" "SELECT COUNT(*) FROM runner_commands WHERE project_id <> 'portfolio-review';")" == "0" ]]

echo "Bird's-eye shell scheduler safety checks passed."

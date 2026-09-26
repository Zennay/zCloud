#!/usr/bin/env bash
set -euo pipefail

ROOT="${ZCLOUD_ROOT:-/home/ubuntu/zennay-cloud}"
DB="${ZCLOUD_DB:-$ROOT/history.db}"
PROJECTS="${ZCLOUD_PROJECTS:-$ROOT/projects.json}"
LAYOUT="${ZCLOUD_LAYOUT:-$ROOT/project-layout.json}"
REVIEW_ID="portfolio-review"
REVIEW_NAME="Portfolio Birdseye Review"
WINDOW_HOURS="${ZCLOUD_BIRDSEYE_WINDOW_HOURS:-12}"
MIN_INTERVAL_HOURS="${ZCLOUD_BIRDSEYE_MIN_INTERVAL_HOURS:-12}"
WAIT_TIMEOUT="${ZCLOUD_BIRDSEYE_WAIT_TIMEOUT:-90}"
DRY_RUN="${ZCLOUD_BIRDSEYE_DRY_RUN:-0}"

log() { printf '[birds-eye] %s\n' "$*"; }
die() { log "ERROR: $*" >&2; exit 1; }

for tool in sqlite3 jq date mktemp sort grep sed paste; do
  command -v "$tool" >/dev/null 2>&1 || die "required tool missing: $tool"
done
[[ -f "$DB" ]] || die "database not found: $DB"
[[ -f "$PROJECTS" ]] || die "projects file not found: $PROJECTS"

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT
visible="$tmpdir/visible-projects"
changed_raw="$tmpdir/changed-projects-raw"
changed="$tmpdir/changed-projects"
prompt_file="$tmpdir/prompt.txt"
project_json_file="$tmpdir/projects.json"

jq -r '.[].id // empty' "$PROJECTS" | sort -u > "$visible"
if [[ -f "$LAYOUT" ]]; then
  jq -r '.archived[]? // empty' "$LAYOUT" | sort -u > "$tmpdir/archived"
  if [[ -s "$tmpdir/archived" ]]; then
    grep -Fvx -f "$tmpdir/archived" "$visible" > "$tmpdir/visible-filtered" || true
    mv "$tmpdir/visible-filtered" "$visible"
  fi
fi

sqlite3 -cmd ".timeout 8000" "$DB" <<SQL
CREATE TABLE IF NOT EXISTS daily_review_runs(
  ts TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  projects TEXT NOT NULL
);
SQL

recent_queued="$(sqlite3 -cmd ".timeout 8000" "$DB" "SELECT COUNT(*) FROM daily_review_runs WHERE status IN ('dispatching','queued') AND unixepoch(ts) > unixepoch('now','-${MIN_INTERVAL_HOURS} hours');")"
if [[ "$recent_queued" != "0" ]]; then
  log "last bounded review is inside the ${MIN_INTERVAL_HOURS}h guard; nothing queued"
  exit 0
fi

{
  sqlite3 -noheader -cmd ".timeout 8000" "$DB" "SELECT DISTINCT project FROM events WHERE unixepoch(ts) >= unixepoch('now','-${WINDOW_HOURS} hours') AND project IS NOT NULL AND project <> '';"
  sqlite3 -noheader -cmd ".timeout 8000" "$DB" "SELECT DISTINCT project_id FROM runner_events WHERE unixepoch(ts) >= unixepoch('now','-${WINDOW_HOURS} hours') AND event IN ('prompt-sent','generation-finished','conversation-adopted') AND project_id IS NOT NULL AND project_id <> 'portfolio-review';"
} | sed '/^$/d' | sort -u > "$changed_raw"

if [[ -s "$changed_raw" ]]; then
  grep -Fxf "$visible" "$changed_raw" | sort -u > "$changed" || true
else
  : > "$changed"
fi

if [[ ! -s "$changed" ]]; then
  ts="$(date -u +%Y-%m-%dT%H:%M:%S%:z)"
  sqlite3 -cmd ".timeout 8000" "$DB" "INSERT OR REPLACE INTO daily_review_runs(ts,status,projects) VALUES('$ts','no_changes','[]');"
  log "no changed active projects in the last ${WINDOW_HOURS}h"
  exit 0
fi

project_text="$(paste -sd ',' "$changed" | sed 's/,/, /g')"
jq -R -s -c 'split("\n") | map(select(length>0))' < "$changed" > "$project_json_file"

cat > "$prompt_file" <<PROMPT
Je bent de Portfolio Bird's-eye Reviewer. Doe precies één bounded review.
Kijk alleen naar lopende projecten die aantoonbaar veranderd zijn in de afgelopen ${WINDOW_HOURS} uur.
zCloud heeft lokaal deze project-ID's met activiteit gevonden: ${project_text}.
Verifieer de actuele waarheid via gekoppelde Notion, GitHub en VPS-context voordat je conclusies trekt.

Doel: helikopterview. Niet opnieuw hard doordrillen op de bestaande aanpak.
Vergelijk patronen tussen projecten en vraag per relevant project of de huidige aanpak nog klopt.
Gebruik de Senior Team OS-regel Periodic Strategy & Architecture Challenge.
Classificeer alleen waar nuttig als KEEP, ADJUST of REDESIGN.
Een slechte losse run is nooit genoeg voor REDESIGN.

Let vooral op herhaalde bottlenecks, dezelfde failure-mechanismen, onnodige complexiteit,
lage informatiewinst per compute/tijd, verkeerde prioriteiten en kansen om simpeler te werken.

Guardrails:
- maak geen productiecodewijzigingen vanuit deze review;
- stop, pauzeer, drain of herstart GEEN gewone projecten of projectworkers;
- wijzig GEEN worker-count, project-active-state of resource-priority van gewone projecten;
- de reviewer mag uitsluitend zijn eigen runner-id portfolio-review starten/pushen;
- verander niet iedere review architectuur om activiteit te creëren;
- verzwak nooit security, provenance, preregistration, anti-leakage of validation gates;
- maak alleen een concrete taak/Notion-update als de evidence een verandering echt rechtvaardigt;
- maximaal 3 cross-project aanbevelingen per review;
- projecten zonder relevante wijziging in ${WINDOW_HOURS} uur niet opnieuw analyseren;
- schrijf voor de gebruiker eerst in gewone Nederlandse taal; technische details compact eronder.

Sluit af met: bekeken projecten, belangrijkste patronen, KEEP/ADJUST/REDESIGN waar relevant,
maximaal 3 acties en wat bewust NIET veranderd hoeft te worden.
Daarna blijft deze reviewchat idle tot de volgende 12-uurs trigger.
PROMPT

sqlite3 -cmd ".timeout 8000" "$DB" <<SQL
INSERT INTO runner_targets(project_id,name,conversation_id,prompt,active,worker_count)
VALUES('$REVIEW_ID','$REVIEW_NAME','',CAST(readfile('$prompt_file') AS TEXT),1,1)
ON CONFLICT(project_id) DO UPDATE SET
  name=excluded.name,
  prompt=excluded.prompt,
  active=1,
  worker_count=1;
INSERT OR IGNORE INTO runner_workers(project_id,worker_slot,conversation_id)
VALUES('$REVIEW_ID',1,'');
SQL

if [[ "$DRY_RUN" == "1" ]]; then
  log "dry-run prepared portfolio-review for: $project_text"
  exit 0
fi

run_ts="$(date -u +%Y-%m-%dT%H:%M:%S%:z)"
sqlite3 -cmd ".timeout 8000" "$DB" "INSERT OR REPLACE INTO daily_review_runs(ts,status,projects) VALUES('$run_ts','dispatching',CAST(readfile('$project_json_file') AS TEXT));"

queue_command() {
  local action="$1"
  [[ "$action" == "start" || "$action" == "push" ]] || die "unsafe reviewer action refused: $action"
  sqlite3 -cmd ".timeout 8000" "$DB" "INSERT INTO runner_commands(project_id,action,status,created_at,updated_at) VALUES('$REVIEW_ID','$action','pending',strftime('%Y-%m-%dT%H:%M:%f+00:00','now'),strftime('%Y-%m-%dT%H:%M:%f+00:00','now')); SELECT last_insert_rowid();"
}

wait_command() {
  local command_id="$1"
  local deadline=$(( $(date +%s) + WAIT_TIMEOUT ))
  local status
  while (( $(date +%s) < deadline )); do
    status="$(sqlite3 -cmd ".timeout 8000" "$DB" "SELECT status FROM runner_commands WHERE id=$command_id;")"
    case "$status" in
      completed) return 0 ;;
      failed) return 1 ;;
    esac
    sleep 2
  done
  return 1
}

start_id="$(queue_command start)"
if ! wait_command "$start_id"; then
  sqlite3 -cmd ".timeout 8000" "$DB" "UPDATE daily_review_runs SET status='failed' WHERE ts='$run_ts';"
  die "portfolio-review start command did not complete; push not queued"
fi

sleep 8
push_id="$(queue_command push)"
sqlite3 -cmd ".timeout 8000" "$DB" "UPDATE daily_review_runs SET status='queued' WHERE ts='$run_ts';"
log "queued one 12h bird's-eye review for: $project_text; push_command=$push_id"

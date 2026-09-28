#!/usr/bin/env bash
set -euo pipefail

ROOT="${ZCLOUD_ROOT:-/home/ubuntu/zennay-cloud}"
DB="${ZCLOUD_DB:-$ROOT/history.db}"
PROJECTS="${ZCLOUD_PROJECTS:-$ROOT/projects.json}"
LAYOUT="${ZCLOUD_LAYOUT:-$ROOT/project-layout.json}"
REVIEW_ID="portfolio-review"
REVIEW_NAME="Portfolio Director"
WINDOW_HOURS="${ZCLOUD_BIRDSEYE_WINDOW_HOURS:-24}"
MIN_INTERVAL_MINUTES="${ZCLOUD_BIRDSEYE_MIN_INTERVAL_MINUTES:-40}"
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

recent_queued="$(sqlite3 -cmd ".timeout 8000" "$DB" "SELECT COUNT(*) FROM daily_review_runs WHERE status IN ('dispatching','queued') AND unixepoch(ts) > unixepoch('now','-${MIN_INTERVAL_MINUTES} minutes');")"
if [[ "$recent_queued" != "0" ]]; then
  log "last portfolio-director review is inside the ${MIN_INTERVAL_MINUTES}m guard; nothing queued"
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
Je bent de zCloud Portfolio Director: de derde, onafhankelijke strategische chat naast maximaal twee uitvoerende portfolio-workers.
Je voert precies één portfolio-review uit en gaat daarna weer idle. Normale backlog-items uitvoeren is NIET jouw rol.

Lees eerst de centrale Notion Portfolio Work Queue:
https://app.notion.com/p/4162fac179f44fcbbe4072a183d2b440
Lees daarna de actuele Projects-database, canonical Project HQ/Handoff van relevante projecten, open claims/PRs en beschikbare VPS/runtime evidence.
zCloud zag in de afgelopen ${WINDOW_HOURS} uur activiteit in: ${project_text}. Gebruik dat alleen als signaal; controleer ook of een stil project
door een belangrijke blocker/deadline/prioriteitsverschuiving ten onrechte uit beeld raakt.

Jouw drie verantwoordelijkheden:
1. PRIORITEIT: beoordeel of P0/P1/P2/P3, Eligible, Why now, Dependencies en Recheck After in de centrale queue nog kloppen.
   Herprioriteer alleen wanneer actuele evidence dit rechtvaardigt. Maximaal twee uitvoerende workers blijven de queue uitvoeren.
2. RICHTING: doe een compacte portfolio/project-audit. Vraag of doel, architectuur, roadmap, experimenten en gekozen aanpak nog steeds
   de beste bekende keuze zijn gegeven nieuwe evidence. Zoek vooral naar verkeerde aannames, lokale optimalisatie, eindeloos repareren,
   onnodige complexiteit, lage informatiewinst en werk dat niet meer naar het echte doel leidt.
3. SENIOR ESCALATION: als er echte strategische/architectuur/product/security/data twijfel is, stuur een gerichte Senior Team OS review
   op dat specifieke vraagstuk aan. Senior review moet eindigen in KEEP, ADJUST of REDESIGN met evidence en concrete queue-impact.
   Roep seniors NIET op voor routinewerk, statusupdates of omdat 40 minuten verstreken zijn.

Anti-churn / autonomie-regels:
- De default is KEEP. Geen wijziging is een geldige en vaak gewenste audituitkomst.
- Maak niet elke review nieuwe architectuur, nieuwe taken of nieuwe prioriteiten.
- REDESIGN vereist sterke nieuwe evidence of een herhaald structureel failure-mechanisme; één slechte run is nooit genoeg.
- Maximaal 3 inhoudelijke portfolio-aanpassingen per review, tenzij een P0 incident/security/integrity probleem meer vereist.
- Verwijder/merge/drop dubbele of obsolete queue-items in plaats van de backlog steeds groter te maken.
- Zet Done nooit zonder verifieerbare completion evidence.
- Als een taak deterministisch/repeatable kan worden uitgevoerd, geef VPS/service/timer/queue/self-hosted GitHub Actions de voorkeur.
  ChatGPT-workers zijn voor research, ontwerp, review, diagnose en andere reasoning-gates.
- Stop/pauzeer/drain/herstart geen gewone projecten of uitvoerende workers vanuit deze review.
- Wijzig geen worker-count of global worker cap. De twee uitvoerende workers blijven maximaal twee.
- De Portfolio Director telt apart als derde strategische chat en claimt geen gewone uitvoeringstaak.
- Verzwak nooit security, provenance, preregistration, anti-leakage, validation of human-approval gates.
- Een menselijke/externe gate blijft Blocked/Eligible=false totdat er echt nieuwe evidence is.

Schrijf de audituitkomst terug naar de centrale queue en relevante canonical Handoff/strategy docs wanneer er iets materieels verandert.
Als niets materieels verandert, leg alleen een compacte audit-evidence vast; manufacture geen werk.

Sluit af met: bekeken projecten; prioriteitswijzigingen; KEEP/ADJUST/REDESIGN per relevante afwijking; eventuele Senior Team OS escalaties;
maximaal 3 acties; wat bewust NIET veranderd is; en welke deterministische stappen door de VPS blijven lopen.
Daarna blijft deze reviewchat idle tot de volgende ongeveer 40-minuten trigger.
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

#!/usr/bin/env python3
"""Trigger one bounded portfolio bird's-eye ChatGPT review every 24h."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import sqlite3
import time

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "history.db"
PROJECTS = ROOT / "projects.json"
LAYOUT = ROOT / "project-layout.json"
REVIEW_ID = "portfolio-review"
REVIEW_NAME = "Daily Bird's-eye Review"
MIN_INTERVAL = timedelta(hours=23)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect():
    conn = sqlite3.connect(DB, timeout=8)
    conn.row_factory = sqlite3.Row
    return conn


def visible_project_ids() -> list[str]:
    projects = json.loads(PROJECTS.read_text())
    ids = [p["id"] for p in projects]
    try:
        archived = set(json.loads(LAYOUT.read_text()).get("archived", []))
    except Exception:
        archived = set()
    return [pid for pid in ids if pid not in archived]


def changed_projects(conn: sqlite3.Connection) -> list[str]:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    visible = set(visible_project_ids())
    changed = {
        r["project"]
        for r in conn.execute(
            "SELECT DISTINCT project FROM events WHERE ts>=? AND project IS NOT NULL",
            (cutoff,),
        )
        if r["project"] in visible
    }
    meaningful = ("prompt-sent", "generation-finished", "conversation-adopted")
    placeholders = ",".join("?" for _ in meaningful)
    rows = conn.execute(
        f"SELECT DISTINCT project_id FROM runner_events "
        f"WHERE ts>=? AND event IN ({placeholders}) AND project_id IS NOT NULL",
        (cutoff, *meaningful),
    )
    changed.update(r["project_id"] for r in rows if r["project_id"] in visible)
    return sorted(changed)


def build_prompt(project_ids: list[str]) -> str:
    project_text = ", ".join(project_ids) if project_ids else "geen"
    return f"""Je bent de dagelijkse Portfolio Bird's-eye Reviewer. Doe precies één bounded review.
Kijk alleen naar lopende projecten die aantoonbaar veranderd zijn in de afgelopen 24 uur.
zCloud heeft lokaal deze project-ID's met activiteit gevonden: {project_text}.
Verifieer de actuele waarheid via gekoppelde Notion, GitHub en VPS-context voordat je conclusies trekt.

Doel: helikopterview. Niet opnieuw hard doordrillen op de bestaande aanpak.
Vergelijk patronen tussen projecten en vraag per relevant project of de huidige aanpak nog klopt.
Gebruik de Senior Team OS-regel Periodic Strategy & Architecture Challenge.
Classificeer alleen waar nuttig als KEEP, ADJUST of REDESIGN.
Een slechte losse run is nooit genoeg voor REDESIGN.

Let vooral op herhaalde bottlenecks, dezelfde failure-mechanismen, onnodige complexiteit,
lage informatiewinst per compute/tijd, verkeerde prioriteiten en kansen om simpeler te werken.

Guardrails:
- maak geen productiecodewijzigingen vanuit deze dagelijkse review;
- verander niet dagelijks architectuur om activiteit te creëren;
- verzwak nooit security, provenance, preregistration, anti-leakage of validation gates;
- maak alleen een concrete taak/Notion-update als de evidence een verandering echt rechtvaardigt;
- maximaal 3 cross-project aanbevelingen per review;
- projecten zonder relevante wijziging in 24 uur niet opnieuw analyseren;
- schrijf voor de gebruiker eerst in gewone Nederlandse taal; technische details compact eronder.

Sluit af met: bekeken projecten, belangrijkste patronen, KEEP/ADJUST/REDESIGN waar relevant,
maximaal 3 acties en wat bewust NIET veranderd hoeft te worden. Daarna wacht je tot de volgende 24-uurs trigger."""


def queue_command(conn: sqlite3.Connection, action: str) -> int:
    ts = now()
    cur = conn.execute(
        "INSERT INTO runner_commands(project_id,action,status,created_at,updated_at) "
        "VALUES(?,?,?,?,?)",
        (REVIEW_ID, action, "pending", ts, ts),
    )
    conn.commit()
    return int(cur.lastrowid)


def wait_command(conn: sqlite3.Connection, command_id: int, timeout: int = 90) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        row = conn.execute(
            "SELECT status FROM runner_commands WHERE id=?", (command_id,)
        ).fetchone()
        if row and row["status"] in ("completed", "failed"):
            return row["status"] == "completed"
        time.sleep(2)
    return False


def main() -> int:
    with connect() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS daily_review_runs("
            "ts TEXT PRIMARY KEY, status TEXT NOT NULL, projects TEXT NOT NULL)"
        )
        last = conn.execute(
            "SELECT ts FROM daily_review_runs WHERE status='queued' ORDER BY ts DESC LIMIT 1"
        ).fetchone()
        if last and datetime.now(timezone.utc) - datetime.fromisoformat(last["ts"]) < MIN_INTERVAL:
            return 0

        projects = changed_projects(conn)
        if not projects:
            conn.execute(
                "INSERT OR REPLACE INTO daily_review_runs VALUES(?,?,?)",
                (now(), "no_changes", "[]"),
            )
            conn.commit()
            return 0

        prompt = build_prompt(projects)
        conn.execute(
            "INSERT INTO runner_targets(project_id,name,conversation_id,prompt,active,worker_count) "
            "VALUES(?,?,?,?,1,1) "
            "ON CONFLICT(project_id) DO UPDATE SET name=excluded.name,prompt=excluded.prompt,active=1,worker_count=1",
            (REVIEW_ID, REVIEW_NAME, "", prompt),
        )
        conn.execute(
            "INSERT OR IGNORE INTO runner_workers(project_id,worker_slot,conversation_id) VALUES(?,?,?)",
            (REVIEW_ID, 1, ""),
        )
        conn.commit()

        start_id = queue_command(conn, "start")
        wait_command(conn, start_id, 90)
        time.sleep(8)
        push_id = queue_command(conn, "push")
        conn.execute(
            "INSERT OR REPLACE INTO daily_review_runs VALUES(?,?,?)",
            (now(), "queued", json.dumps(projects)),
        )
        conn.commit()
        print(f"queued daily bird's-eye review: {projects}; command={push_id}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

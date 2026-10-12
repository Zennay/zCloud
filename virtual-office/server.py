#!/usr/bin/env python3
"""Virtual Office: durable local task queue + Codex (ChatGPT subscription) worker.

No OpenAI API key is used. Codex CLI must be installed and signed in by the
host owner. Never expose this service directly without an access token + TLS.
"""
from __future__ import annotations

import argparse
import hmac
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent
DB = Path(os.getenv("OFFICE_DB_PATH", str(ROOT / "office.db"))).expanduser().resolve()
WORKSPACE = Path(os.getenv("OFFICE_WORKSPACE_ROOT", str(ROOT / "workspace"))).expanduser().resolve()
BIND = os.getenv("OFFICE_BIND", "127.0.0.1")
PORT = int(os.getenv("OFFICE_PORT", "8799"))
TOKEN = os.getenv("OFFICE_ACCESS_TOKEN", "")
ALLOW_WRITES = os.getenv("OFFICE_ALLOW_WRITES", "0") == "1"
PERSONAS = {
    "maya": {"id": "maya", "name": "Maya", "title": "Projectmanager", "role": "Planning, overzicht en prioriteiten", "personality": "Warm, proactief, gestructureerd. Vraagt door bij onduidelijke doelen.", "color": "#f0a878", "x": -4.1, "z": -2.9},
    "noah": {"id": "noah", "name": "Noah", "title": "Software engineer", "role": "Bouwt en verbetert software", "personality": "Technisch, nuchter, stelt scherpe vragen, onderbouwt wijzigingen.", "color": "#78b8d6", "x": 0.15, "z": -2.9},
    "lina": {"id": "lina", "name": "Lina", "title": "QA & design", "role": "Test, valideert en verbetert UX", "personality": "Kritisch maar vriendelijk, detailgericht en toegankelijk.", "color": "#b6a0e5", "x": 4.1, "z": -2.9},
}
AUTH_CACHE = (0.0, False)


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(DB), timeout=10, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=10000")
    db.execute("PRAGMA journal_mode=WAL")
    return db


def init_db() -> None:
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, employee TEXT NOT NULL, kind TEXT NOT NULL,
            instruction TEXT NOT NULL, status TEXT NOT NULL,
            output TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_jobs_queue ON jobs(status, created_at);
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee TEXT NOT NULL, role TEXT NOT NULL,
            content TEXT NOT NULL, created_at TEXT NOT NULL
        );
        """)
        # A process may have died mid-execution. Never replay a writing job
        # automatically: the previous run might already have changed files.
        db.execute("UPDATE jobs SET status='interrupted', updated_at=? WHERE status='running'", (timestamp(),))


def available_codex(force: bool = False) -> bool:
    global AUTH_CACHE
    now = time.monotonic()
    if not force and now - AUTH_CACHE[0] < 20:
        return AUTH_CACHE[1]
    cli = shutil.which("codex")
    if not cli:
        AUTH_CACHE = (now, False)
        return False
    try:
        status = subprocess.run([cli, "login", "status"], text=True, capture_output=True, timeout=8)
        ready = status.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        ready = False
    AUTH_CACHE = (now, ready)
    return ready


def enqueue(employee: str, kind: str, instruction: str) -> dict:
    if employee not in PERSONAS or kind not in ("chat", "task"):
        raise ValueError("Onbekende medewerker of opdrachttype.")
    instruction = instruction.strip()
    if not 1 <= len(instruction) <= 3500:
        raise ValueError("Een opdracht moet 1–3500 tekens bevatten.")
    if kind == "task" and not ALLOW_WRITES:
        raise ValueError("Schrijftaken staan uit. Zet OFFICE_ALLOW_WRITES=1 op een geïsoleerde werkmap.")
    ident, now = uuid.uuid4().hex, timestamp()
    with connect() as db:
        db.execute("INSERT INTO jobs(id,employee,kind,instruction,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                   (ident, employee, kind, instruction, "queued", now, now))
        if kind == "chat":
            db.execute("INSERT INTO messages(employee,role,content,created_at) VALUES (?,?,?,?)",
                       (employee, "user", instruction, now))
    return get_job(ident)


def get_job(ident: str) -> dict | None:
    with connect() as db:
        row = db.execute("SELECT * FROM jobs WHERE id=?", (ident,)).fetchone()
        return dict(row) if row else None


def list_jobs(limit: int = 20) -> list:
    with connect() as db:
        return [dict(x) for x in db.execute("SELECT * FROM jobs ORDER BY created_at DESC,id DESC LIMIT ?", (max(1, min(limit, 50)),))]


def retry(ident: str) -> dict | None:
    now = timestamp()
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("UPDATE jobs SET status='queued', error='',updated_at=? WHERE id=? AND status IN ('failed','interrupted','waiting_auth')", (now, ident))
        if db.execute("SELECT changes()").fetchone()[0] == 0:
            db.execute("COMMIT")
            return None
        db.execute("COMMIT")
    return get_job(ident)


def claim() -> dict | None:
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM jobs WHERE status IN ('queued','waiting_auth') ORDER BY created_at,id LIMIT 1").fetchone()
        if row:
            db.execute("UPDATE jobs SET status='running',updated_at=?,attempts=attempts+1 WHERE id=?", (timestamp(), row["id"]))
        db.execute("COMMIT")
        return dict(row) if row else None


def finish(ident: str, status: str, output: str = "", error: str = "") -> None:
    if status not in ("done", "failed", "waiting_auth"):
        raise ValueError("Invalid final state")
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        current = db.execute("SELECT employee,kind FROM jobs WHERE id=? AND status='running'", (ident,)).fetchone()
        if current:
            db.execute("UPDATE jobs SET status=?,output=?,error=?,updated_at=? WHERE id=?",
                       (status, output[:15000], error[:1000], timestamp(), ident))
            if status == "done" and current["kind"] == "chat":
                db.execute("INSERT INTO messages(employee,role,content,created_at) VALUES (?,?,?,?)",
                           (current["employee"], "assistant", output[:8000], timestamp()))
        db.execute("COMMIT")


def prompt_for(job: dict) -> str:
    persona = PERSONAS[job["employee"]]
    with connect() as db:
        history = [dict(r) for r in db.execute(
            "SELECT role,content FROM messages WHERE employee=? ORDER BY id DESC LIMIT 12", (job["employee"],))]
    history.reverse()
    context = "\n".join(m["role"] + ": " + m["content"][:1000] for m in history)
    instructions = (
        "Je bent " + persona["name"] + ", " + persona["title"] + " in een virtueel AI-kantoor. "
        + persona["personality"] + " Antwoord in het Nederlands, natuurlijk en beknopt. "
        + "Je hebt geen toegang tot live projectstatus buiten de huidige werkmap. Verzin geen uitgevoerde taken. "
        + "Noem blokkades eerlijk. Werk niet buiten de huidige map. "
    )
    if job["kind"] == "task":
        instructions += (
            "Dit is een ECHTE programmeeropdracht. Wijzig bestanden uitsluitend in de ingestelde sandbox-werkmap; "
            "voer geschikte lokale tests uit; push, deploy, verwijder niets extern en gebruik geen secrets. "
            "Beschrijf na afloop exact welke bestanden je hebt gewijzigd en wat je hebt getest. "
        )
    return instructions + "\nGespreksgeschiedenis:\n" + context + "\nNieuwe opdracht:\n" + job["instruction"]


def execute(job: dict) -> str:
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    cli = shutil.which("codex")
    if not cli:
        raise RuntimeError("Codex CLI ontbreekt")
    sandbox = "workspace-write" if job["kind"] == "task" and ALLOW_WRITES else "read-only"
    with tempfile.TemporaryDirectory(prefix="virtual-office-") as folder:
        output_file = Path(folder) / "answer.txt"
        # Codex exec supports ChatGPT-account login; no API key is injected.
        proc = subprocess.run(
            [cli, "exec", "--skip-git-repo-check", "--sandbox", sandbox,
             "--output-last-message", str(output_file), "-"],
            input=prompt_for(job), text=True, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, cwd=str(WORKSPACE), timeout=360,
        )
        if proc.returncode:
            raise RuntimeError("Codex exit code " + str(proc.returncode) + " (controleer login en CLI-versie)")
        answer = output_file.read_text(encoding="utf-8").strip() if output_file.exists() else ""
        if not answer:
            raise RuntimeError("Codex gaf geen eindantwoord terug")
        return answer


def work_forever(stop: threading.Event | None = None) -> None:
    while stop is None or not stop.is_set():
        if not available_codex():
            if stop:
                stop.wait(15)
            else:
                time.sleep(15)
            continue
        job = claim()
        if not job:
            if stop:
                stop.wait(1)
            else:
                time.sleep(1)
            continue
        try:
            finish(job["id"], "done", output=execute(job))
        except subprocess.TimeoutExpired:
            finish(job["id"], "failed", error="Codex-taak duurde langer dan 6 minuten.")
        except Exception as exc:
            finish(job["id"], "failed", error=str(exc)[:500])


class Handler(BaseHTTPRequestHandler):
    server_version = "VirtualOffice/0.1"

    def json_reply(self, code: int, obj: object) -> None:
        payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)

    def authorized(self) -> bool:
        if not TOKEN:
            return True  # Server cannot bind publicly without a token.
        supplied = self.headers.get("X-Office-Token", "")
        return hmac.compare_digest(TOKEN, supplied)

    def require_auth(self) -> bool:
        if not self.authorized():
            self.json_reply(401, {"error": "Voer je Office-toegangscode in."})
            return False
        return True

    def do_GET(self) -> None:
        url = urlsplit(self.path)
        if url.path in ("/", "/index.html"):
            body = (ROOT / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if url.path == "/api/health":
            self.json_reply(200, {"online": True, "auth_required": bool(TOKEN),
                                  "codex_ready": available_codex(), "tasks_enabled": ALLOW_WRITES})
            return
        if not self.require_auth():
            return
        if url.path == "/api/employees":
            self.json_reply(200, {"employees": list(PERSONAS.values())})
        elif url.path == "/api/jobs":
            self.json_reply(200, {"jobs": list_jobs()})
        elif url.path.startswith("/api/jobs/"):
            job = get_job(url.path.split("/")[-1])
            self.json_reply(200 if job else 404, job if job else {"error": "Niet gevonden"})
        elif url.path == "/api/messages":
            employee = parse_qs(url.query).get("employee", [""])[0]
            if employee not in PERSONAS:
                self.json_reply(400, {"error": "Onbekende medewerker"})
                return
            with connect() as db:
                rows = [dict(r) for r in db.execute(
                    "SELECT role,content,created_at FROM messages WHERE employee=? ORDER BY id DESC LIMIT 30",
                    (employee,))]
            self.json_reply(200, {"messages": list(reversed(rows))})
        else:
            self.json_reply(404, {"error": "Niet gevonden"})

    def do_POST(self) -> None:
        url = urlsplit(self.path)
        if not self.require_auth():
            return
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            self.json_reply(415, {"error": "Alleen application/json wordt ondersteund"})
            return
        length = self.headers.get("Content-Length", "")
        if not length.isdigit() or int(length) > 10000:
            self.json_reply(413, {"error": "Ongeldige of te grote aanvraag"})
            return
        try:
            body = json.loads(self.rfile.read(int(length)))
            if not isinstance(body, dict):
                raise ValueError("JSON object verwacht")
            if url.path == "/api/jobs":
                self.json_reply(201, enqueue(body.get("employee", ""), body.get("kind", "chat"), body.get("instruction", "")))
            elif url.path.startswith("/api/jobs/") and url.path.endswith("/retry"):
                ident = url.path.split("/")[-2]
                job = retry(ident)
                self.json_reply(200 if job else 409, job or {"error": "Taak kan niet opnieuw worden gestart"})
            else:
                self.json_reply(404, {"error": "Niet gevonden"})
        except (ValueError, TypeError, KeyError) as exc:
            self.json_reply(400, {"error": str(exc)})

    def log_message(self, fmt: str, *args: object) -> None:
        pass  # Avoid request paths or token mishaps in public logs.


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("mode", nargs="?", default="all", choices=["all", "server", "worker"])
    args = p.parse_args()
    if BIND not in ("127.0.0.1", "::1", "localhost") and not TOKEN:
        p.error("OFFICE_ACCESS_TOKEN is verplicht bij een niet-lokale bind.")
    init_db()
    if args.mode == "worker":
        work_forever()
    else:
        if args.mode == "all":
            threading.Thread(target=work_forever, name="office-codex-worker", daemon=True).start()
        with ThreadingHTTPServer((BIND, PORT), Handler) as http:
            print("Virtual Office op http://" + BIND + ":" + str(PORT), flush=True)
            http.serve_forever()


if __name__ == "__main__":
    main()
